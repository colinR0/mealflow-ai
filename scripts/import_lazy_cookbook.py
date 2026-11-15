"""把前端「懒人宝典」的菜谱数据集接入后端菜谱库，供 Agent 智能配餐使用。

数据源：apps/web/public/data/recipes_merged.json（cook + HowToCook 合并，937 条）

设计约束：
- 只读源文件，绝不修改它。
- 只新增 recipes / ingredients / recipe_ingredients 记录；不删改任何既有菜谱
  （同名菜谱直接跳过，保护 data/seed 的 32 道演示菜谱）。
- 源数据没有用量与价格，因此不编造：quantity 留空单位、kcal 与成本留 None，
  portion_confidence 自然落为 servings_only，界面显示「按 N 人份准备」。
- 食材必须归一化到与种子数据一致的 key（虾→shrimp、香菜→cilantro），
  否则成员过敏与忌口的硬过滤对新菜谱失效。

用法：
  # 先看统计，不写库
  python scripts/import_lazy_cookbook.py
  # 确认无误后真正导入
  python scripts/import_lazy_cookbook.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from sqlalchemy import select  # noqa: E402

from app.services.recipe_taxonomy import IngredientFact, classify_recipe  # noqa: E402

SOURCE_PATH = REPO_ROOT / "apps" / "web" / "public" / "data" / "recipes_merged.json"

SOURCE_NAME = "懒人宝典（cook + HowToCook 合并数据集）"
LICENSE_NOTE = "来源为公开菜谱数据集（cook / HowToCook），仅用于演示"

# 非正餐类别不进配餐池（饮品/甜品/酱料/半成品）
SKIP_CATEGORIES = {"饮品", "甜品", "酱料", "半成品"}

# 数据集里混入了炼油、汤底这类"不是菜"的条目，不能进正餐配餐池
NOT_DISH_TOKENS = ("炼猪油", "熬猪油", "炸猪油", "猪油", "油渣", "糖色", "高汤", "熬糖")

# 源数据 stuff 字段混入了厨具、容器、括号备注等噪音，直接丢弃
NOISE_EXACT = {
    "", "..", "...", "原料:", "原料", "可选）", "装饰用）", "水", "清水", "开水", "热水",
    "饮用水", "自来水", "冰块", "冰水", "烤箱", "电饭煲", "空气炸锅", "高压锅", "微波炉",
    "平底锅", "炒锅", "蒸锅", "搅拌机", "锡纸盘", "擀面杖", "保鲜膜", "手动压汁器",
    "小碗若干", "大碗", "碗", "盘子", "厨房纸巾", "牙签", "锡纸", "油纸", "硅胶铲",
    "刀和砧板", "砧板", "菜刀", "蒸锅或电蒸炉", "电蒸炉", "铲子", "漏勺", "勺子",
    "汤勺", "筷子", "模具", "滤网", "刷子", "手套", "密封罐", "玻璃碗",
}
NOISE_PATTERN = re.compile(r"[（(].*?[)）]|[（(].*$|^\d+[\u4e00-\u9fa5]*$|^\d")
TOOL_SUFFIX = ("锅", "机", "器", "盘", "碗", "铲", "纸", "膜", "杖", "签", "布",
               "炉", "刀", "勺", "板", "夹", "罐", "网", "刷")

# 中文食材名 -> (key, 规范名, 类别, 默认单位)
# key 与 data/seed/recipes.json 保持一致，保证过敏/忌口硬过滤命中
INGREDIENT_MAP: dict[str, tuple[str, str, str, str]] = {
    # 蔬菜
    "土豆": ("potato", "土豆", "蔬菜", "kg"),
    "马铃薯": ("potato", "土豆", "蔬菜", "kg"),
    "番茄": ("tomato", "番茄", "蔬菜", "kg"),
    "西红柿": ("tomato", "番茄", "蔬菜", "kg"),
    "圣女果": ("tomato", "番茄", "蔬菜", "kg"),
    "青菜": ("greens", "青菜", "蔬菜", "kg"),
    "小白菜": ("greens", "青菜", "蔬菜", "kg"),
    "油菜": ("greens", "青菜", "蔬菜", "kg"),
    "上海青": ("greens", "青菜", "蔬菜", "kg"),
    "生菜": ("lettuce", "生菜", "蔬菜", "kg"),
    "油麦菜": ("lettuce", "生菜", "蔬菜", "kg"),
    "豆角": ("green_beans", "豆角", "蔬菜", "kg"),
    "四季豆": ("green_beans", "豆角", "蔬菜", "kg"),
    "青椒": ("pepper_green", "青椒", "蔬菜", "kg"),
    "菜椒": ("pepper_green", "青椒", "蔬菜", "kg"),
    "柿子椒": ("pepper_green", "青椒", "蔬菜", "kg"),
    "胡萝卜": ("carrot", "胡萝卜", "蔬菜", "kg"),
    "红萝卜": ("carrot", "胡萝卜", "蔬菜", "kg"),
    "洋葱": ("onion", "洋葱", "蔬菜", "kg"),
    "黄瓜": ("cucumber", "黄瓜", "蔬菜", "kg"),
    "青瓜": ("cucumber", "黄瓜", "蔬菜", "kg"),
    "茄子": ("eggplant", "茄子", "蔬菜", "kg"),
    "冬瓜": ("winter_melon", "冬瓜", "蔬菜", "kg"),
    "南瓜": ("pumpkin", "南瓜", "蔬菜", "kg"),
    "西葫芦": ("zucchini", "西葫芦", "蔬菜", "kg"),
    "白菜": ("cabbage", "白菜", "蔬菜", "kg"),
    "大白菜": ("cabbage", "白菜", "蔬菜", "kg"),
    "娃娃菜": ("cabbage", "白菜", "蔬菜", "kg"),
    "包菜": ("cabbage", "白菜", "蔬菜", "kg"),
    "卷心菜": ("cabbage", "白菜", "蔬菜", "kg"),
    "圆白菜": ("cabbage", "白菜", "蔬菜", "kg"),
    "花菜": ("cauliflower", "花菜", "蔬菜", "kg"),
    "菜花": ("cauliflower", "花菜", "蔬菜", "kg"),
    "西兰花": ("broccoli", "西兰花", "蔬菜", "kg"),
    "西蓝花": ("broccoli", "西兰花", "蔬菜", "kg"),
    "白萝卜": ("radish", "白萝卜", "蔬菜", "kg"),
    "萝卜": ("radish", "白萝卜", "蔬菜", "kg"),
    "青萝卜": ("radish", "白萝卜", "蔬菜", "kg"),
    "芹菜": ("celery", "芹菜", "蔬菜", "kg"),
    "西芹": ("celery", "芹菜", "蔬菜", "kg"),
    "莴笋": ("lettuce_stem", "莴笋", "蔬菜", "kg"),
    "莴苣": ("lettuce_stem", "莴笋", "蔬菜", "kg"),
    "苦瓜": ("bitter_melon", "苦瓜", "蔬菜", "kg"),
    "丝瓜": ("loofah", "丝瓜", "蔬菜", "kg"),
    "秋葵": ("okra", "秋葵", "蔬菜", "kg"),
    "莲藕": ("lotus_root", "莲藕", "蔬菜", "kg"),
    "藕": ("lotus_root", "莲藕", "蔬菜", "kg"),
    "山药": ("yam", "山药", "蔬菜", "kg"),
    "芋头": ("taro", "芋头", "蔬菜", "kg"),
    "菠菜": ("spinach", "菠菜", "蔬菜", "kg"),
    "韭菜": ("chives", "韭菜", "蔬菜", "kg"),
    "蒜苗": ("garlic_sprout", "蒜苗", "蔬菜", "kg"),
    "蒜苔": ("garlic_sprout", "蒜苗", "蔬菜", "kg"),
    "豆芽": ("bean_sprout", "豆芽", "蔬菜", "kg"),
    "绿豆芽": ("bean_sprout", "豆芽", "蔬菜", "kg"),
    "黄豆芽": ("bean_sprout", "豆芽", "蔬菜", "kg"),
    "玉米": ("corn", "玉米", "蔬菜", "kg"),
    "玉米粒": ("corn", "玉米", "蔬菜", "kg"),
    "芦笋": ("asparagus", "芦笋", "蔬菜", "kg"),
    "茭白": ("water_bamboo", "茭白", "蔬菜", "kg"),
    "竹笋": ("bamboo_shoot", "竹笋", "蔬菜", "kg"),
    "冬笋": ("bamboo_shoot", "竹笋", "蔬菜", "kg"),
    "木耳": ("wood_ear", "木耳", "蔬菜", "kg"),
    "黑木耳": ("wood_ear", "木耳", "蔬菜", "kg"),
    "银耳": ("white_fungus", "银耳", "蔬菜", "kg"),
    "香菇": ("mushroom", "香菇", "蔬菜", "kg"),
    "蘑菇": ("mushroom", "香菇", "蔬菜", "kg"),
    "口蘑": ("mushroom", "香菇", "蔬菜", "kg"),
    "杏鲍菇": ("king_oyster", "杏鲍菇", "蔬菜", "kg"),
    "金针菇": ("enoki", "金针菇", "蔬菜", "kg"),
    "平菇": ("oyster_mushroom", "平菇", "蔬菜", "kg"),
    "菌菇": ("mushroom", "香菇", "蔬菜", "kg"),
    "香菜": ("cilantro", "香菜", "蔬菜", "kg"),
    "芫荽": ("cilantro", "香菜", "蔬菜", "kg"),
    # 辛香料（芳香类，不参与蔬菜计数）
    "大蒜": ("garlic", "大蒜", "蔬菜", "kg"),
    "蒜": ("garlic", "大蒜", "蔬菜", "kg"),
    "蒜头": ("garlic", "大蒜", "蔬菜", "kg"),
    "蒜瓣": ("garlic", "大蒜", "蔬菜", "kg"),
    "蒜末": ("garlic", "大蒜", "蔬菜", "kg"),
    "蒜蓉": ("garlic", "大蒜", "蔬菜", "kg"),
    "蒜片": ("garlic", "大蒜", "蔬菜", "kg"),
    "蒜粉": ("garlic", "大蒜", "蔬菜", "kg"),
    "生姜": ("ginger", "生姜", "蔬菜", "kg"),
    "姜": ("ginger", "生姜", "蔬菜", "kg"),
    "姜片": ("ginger", "生姜", "蔬菜", "kg"),
    "姜末": ("ginger", "生姜", "蔬菜", "kg"),
    "老姜": ("ginger", "生姜", "蔬菜", "kg"),
    "姜蒜": ("ginger", "生姜", "蔬菜", "kg"),
    "小葱": ("scallion", "小葱", "蔬菜", "kg"),
    "葱": ("scallion", "小葱", "蔬菜", "kg"),
    "大葱": ("scallion", "小葱", "蔬菜", "kg"),
    "香葱": ("scallion", "小葱", "蔬菜", "kg"),
    "葱花": ("scallion", "小葱", "蔬菜", "kg"),
    "葱段": ("scallion", "小葱", "蔬菜", "kg"),
    "葱姜蒜": ("scallion", "小葱", "蔬菜", "kg"),
    "干辣椒": ("dried_chili", "干辣椒", "调料", "kg"),
    "辣椒干": ("dried_chili", "干辣椒", "调料", "kg"),
    # 肉禽
    "猪前腿肉": ("pork", "猪前腿肉", "肉禽", "kg"),
    "猪肉": ("pork", "猪前腿肉", "肉禽", "kg"),
    "瘦肉": ("pork", "猪前腿肉", "肉禽", "kg"),
    "猪瘦肉": ("pork", "猪前腿肉", "肉禽", "kg"),
    "肉": ("pork", "猪前腿肉", "肉禽", "kg"),
    "肉末": ("pork", "猪前腿肉", "肉禽", "kg"),
    "肉沫": ("pork", "猪前腿肉", "肉禽", "kg"),
    "猪肉末": ("pork", "猪前腿肉", "肉禽", "kg"),
    "五花肉": ("pork_belly", "五花肉", "肉禽", "kg"),
    "猪五花肉": ("pork_belly", "五花肉", "肉禽", "kg"),
    "带皮五花肉": ("pork_belly", "五花肉", "肉禽", "kg"),
    "排骨": ("ribs", "排骨", "肉禽", "kg"),
    "肋排": ("ribs", "排骨", "肉禽", "kg"),
    "骨头": ("ribs", "排骨", "肉禽", "kg"),
    "大骨头": ("ribs", "排骨", "肉禽", "kg"),
    "培根": ("bacon", "培根", "肉禽", "kg"),
    "火腿": ("ham", "火腿", "肉禽", "kg"),
    "火腿肠": ("ham", "火腿", "肉禽", "kg"),
    "午餐肉": ("ham", "火腿", "肉禽", "kg"),
    "香肠": ("sausage", "香肠", "肉禽", "kg"),
    "腊肠": ("sausage", "香肠", "肉禽", "kg"),
    "鸡胸肉": ("chicken_breast", "鸡胸肉", "肉禽", "kg"),
    "鸡肉": ("chicken_breast", "鸡胸肉", "肉禽", "kg"),
    "鸡腿": ("chicken_leg", "鸡腿", "肉禽", "kg"),
    "鸡腿肉": ("chicken_leg", "鸡腿", "肉禽", "kg"),
    "鸡翅中": ("chicken_wing", "鸡翅", "肉禽", "kg"),
    "鸡翅": ("chicken_wing", "鸡翅", "肉禽", "kg"),
    "鸡爪": ("chicken_feet", "鸡爪", "肉禽", "kg"),
    "牛腩": ("beef", "牛腩", "肉禽", "kg"),
    "牛肉": ("beef", "牛腩", "肉禽", "kg"),
    "羊肉": ("lamb", "羊肉", "肉禽", "kg"),
    "羊排": ("lamb", "羊肉", "肉禽", "kg"),
    "鸭肉": ("duck", "鸭肉", "肉禽", "kg"),
    "鸭": ("duck", "鸭肉", "肉禽", "kg"),
    # 水产（虾类必须落 key=shrimp，否则虾过敏硬过滤失效）
    "虾": ("shrimp", "虾仁", "水产", "kg"),
    "虾仁": ("shrimp", "虾仁", "水产", "kg"),
    "大虾": ("shrimp", "虾仁", "水产", "kg"),
    "基围虾": ("shrimp", "虾仁", "水产", "kg"),
    "罗氏虾": ("shrimp", "虾仁", "水产", "kg"),
    "虾米": ("shrimp", "虾仁", "水产", "kg"),
    "草鱼": ("fish", "草鱼", "水产", "kg"),
    "鱼": ("fish", "草鱼", "水产", "kg"),
    "鲤鱼": ("fish", "草鱼", "水产", "kg"),
    "鲫鱼": ("fish", "草鱼", "水产", "kg"),
    "鲈鱼": ("fish", "草鱼", "水产", "kg"),
    "带鱼": ("fish", "草鱼", "水产", "kg"),
    "鱼片": ("fish", "草鱼", "水产", "kg"),
    "蟹": ("crab", "蟹", "水产", "kg"),
    "螃蟹": ("crab", "蟹", "水产", "kg"),
    "花甲": ("clam", "蛤蜊", "水产", "kg"),
    "蛤蜊": ("clam", "蛤蜊", "水产", "kg"),
    "蛤": ("clam", "蛤蜊", "水产", "kg"),
    "扇贝": ("scallop", "扇贝", "水产", "kg"),
    "鱿鱼": ("squid", "鱿鱼", "水产", "kg"),
    "海参": ("sea_cucumber", "海参", "水产", "kg"),
    "海带": ("kelp", "海带", "水产干货", "kg"),
    "紫菜": ("seaweed", "紫菜", "水产干货", "kg"),
    # 蛋奶
    "鸡蛋": ("egg", "鸡蛋", "蛋奶", "个"),
    "蛋": ("egg", "鸡蛋", "蛋奶", "个"),
    "鸭蛋": ("egg", "鸡蛋", "蛋奶", "个"),
    "皮蛋": ("century_egg", "皮蛋", "蛋奶", "个"),
    "咸鸭蛋": ("salted_egg", "咸鸭蛋", "蛋奶", "个"),
    "牛奶": ("milk", "牛奶", "蛋奶", "L"),
    "全脂牛奶": ("milk", "牛奶", "蛋奶", "L"),
    "酸奶": ("yogurt", "酸奶", "蛋奶", "L"),
    "原味酸奶": ("yogurt", "酸奶", "蛋奶", "L"),
    "淡奶油": ("cream", "淡奶油", "蛋奶", "L"),
    "芝士": ("cheese", "芝士", "蛋奶", "kg"),
    "奶酪": ("cheese", "芝士", "蛋奶", "kg"),
    "黄油": ("butter", "黄油", "蛋奶", "kg"),
    "无盐黄油": ("butter", "黄油", "蛋奶", "kg"),
    # 豆制品
    "豆腐": ("tofu", "豆腐", "豆制品", "kg"),
    "老豆腐": ("tofu", "豆腐", "豆制品", "kg"),
    "嫩豆腐": ("tofu", "豆腐", "豆制品", "kg"),
    "日本豆腐（玉子豆腐）": ("tofu", "豆腐", "豆制品", "kg"),
    "豆干": ("dried_tofu", "豆干", "豆制品", "kg"),
    "香干": ("dried_tofu", "豆干", "豆制品", "kg"),
    "腐竹": ("tofu_skin", "腐竹", "豆制品", "kg"),
    "豆浆": ("soy_milk", "豆浆", "豆制品", "L"),
    # 主食
    "大米": ("rice", "大米", "主食", "kg"),
    "米": ("rice", "大米", "主食", "kg"),
    "米饭": ("rice", "大米", "主食", "kg"),
    "糯米": ("rice", "大米", "主食", "kg"),
    "小米": ("millet", "小米", "主食", "kg"),
    "挂面": ("noodles", "挂面", "主食", "kg"),
    "面条": ("noodles", "挂面", "主食", "kg"),
    "面食": ("noodles", "挂面", "主食", "kg"),
    "方便面": ("noodles", "挂面", "主食", "kg"),
    "面粉": ("flour", "面粉", "主食", "kg"),
    "中筋面粉": ("flour", "面粉", "主食", "kg"),
    "低筋面粉": ("flour", "面粉", "主食", "kg"),
    "高筋面粉": ("flour", "面粉", "主食", "kg"),
    "面包": ("bread", "面包", "主食", "kg"),
    "馒头": ("steamed_bun", "馒头", "主食", "个"),
    "花卷": ("steamed_bun", "馒头", "主食", "个"),
    "包子": ("steamed_bun", "馒头", "主食", "个"),
    "饺子皮": ("dumpling_wrapper", "饺子皮", "主食", "kg"),
    "粉丝": ("vermicelli", "粉丝", "主食", "kg"),
    "红薯粉条": ("vermicelli", "粉丝", "主食", "kg"),
    # 坚果
    "花生米": ("peanut", "花生米", "坚果", "kg"),
    "花生": ("peanut", "花生米", "坚果", "kg"),
    "芝麻": ("sesame", "芝麻", "坚果", "kg"),
    "白芝麻": ("sesame", "芝麻", "坚果", "kg"),
    "核桃": ("walnut", "核桃", "坚果", "kg"),
    "腰果": ("cashew", "腰果", "坚果", "kg"),
    "葡萄干": ("raisin", "葡萄干", "坚果", "kg"),
    "红枣": ("red_date", "红枣", "坚果", "kg"),
    "枸杞": ("goji", "枸杞", "坚果", "kg"),
    # 调料
    "酱油": ("soy_sauce", "酱油", "调料", "L"),
    "生抽": ("soy_sauce", "酱油", "调料", "L"),
    "生抽酱油": ("soy_sauce", "酱油", "调料", "L"),
    "老抽": ("dark_soy_sauce", "老抽", "调料", "L"),
    "味极鲜": ("soy_sauce", "酱油", "调料", "L"),
    "盐": ("salt", "盐", "调料", "kg"),
    "食盐": ("salt", "盐", "调料", "kg"),
    "食用盐": ("salt", "盐", "调料", "kg"),
    "精盐": ("salt", "盐", "调料", "kg"),
    "油": ("oil", "食用油", "调料", "L"),
    "食用油": ("oil", "食用油", "调料", "L"),
    "植物油": ("oil", "食用油", "调料", "L"),
    "菜籽油": ("oil", "食用油", "调料", "L"),
    "花生油": ("oil", "食用油", "调料", "L"),
    "橄榄油": ("olive_oil", "橄榄油", "调料", "L"),
    "猪油": ("lard", "猪油", "调料", "kg"),
    "香油": ("sesame_oil", "香油", "调料", "L"),
    "芝麻油": ("sesame_oil", "香油", "调料", "L"),
    "芝麻香油": ("sesame_oil", "香油", "调料", "L"),
    "麻油": ("sesame_oil", "香油", "调料", "L"),
    "醋": ("vinegar", "香醋", "调料", "L"),
    "香醋": ("vinegar", "香醋", "调料", "L"),
    "陈醋": ("vinegar", "香醋", "调料", "L"),
    "白醋": ("white_vinegar", "白醋", "调料", "L"),
    "白糖": ("sugar", "白糖", "调料", "kg"),
    "糖": ("sugar", "白糖", "调料", "kg"),
    "白砂糖": ("sugar", "白糖", "调料", "kg"),
    "冰糖": ("rock_sugar", "冰糖", "调料", "kg"),
    "料酒": ("cooking_wine", "料酒", "调料", "L"),
    "黄酒": ("cooking_wine", "料酒", "调料", "L"),
    "啤酒": ("beer", "啤酒", "调料", "L"),
    "蚝油": ("oyster_sauce", "蚝油", "调料", "L"),
    "耗油": ("oyster_sauce", "蚝油", "调料", "L"),
    "豆瓣酱": ("doubanjiang", "豆瓣酱", "调料", "kg"),
    "郫县豆瓣酱": ("doubanjiang", "豆瓣酱", "调料", "kg"),
    "黄豆酱": ("soybean_paste", "黄豆酱", "调料", "kg"),
    "甜面酱": ("sweet_bean_paste", "甜面酱", "调料", "kg"),
    "番茄酱": ("ketchup", "番茄酱", "调料", "kg"),
    "芝麻酱": ("sesame_paste", "芝麻酱", "调料", "kg"),
    "豆豉": ("fermented_black_bean", "豆豉", "调料", "kg"),
    "淀粉": ("starch", "淀粉", "调料", "kg"),
    "生粉": ("starch", "淀粉", "调料", "kg"),
    "玉米淀粉": ("starch", "淀粉", "调料", "kg"),
    "土豆淀粉": ("starch", "淀粉", "调料", "kg"),
    "鸡精": ("chicken_powder", "鸡精", "调料", "kg"),
    "味精": ("msg", "味精", "调料", "kg"),
    "花椒": ("sichuan_pepper", "花椒", "调料", "kg"),
    "青花椒": ("sichuan_pepper", "花椒", "调料", "kg"),
    "花椒粉": ("sichuan_pepper", "花椒", "调料", "kg"),
    "花椒碎": ("sichuan_pepper", "花椒", "调料", "kg"),
    "花椒油": ("sichuan_pepper_oil", "花椒油", "调料", "L"),
    "藤椒油": ("sichuan_pepper_oil", "花椒油", "调料", "L"),
    "八角": ("star_anise", "八角", "调料", "kg"),
    "大料": ("star_anise", "八角", "调料", "kg"),
    "桂皮": ("cinnamon", "桂皮", "调料", "kg"),
    "肉桂棒": ("cinnamon", "桂皮", "调料", "kg"),
    "香叶": ("bay_leaf", "香叶", "调料", "kg"),
    "丁香": ("clove", "丁香", "调料", "kg"),
    "小茴香": ("fennel", "小茴香", "调料", "kg"),
    "孜然": ("cumin", "孜然", "调料", "kg"),
    "孜然粉": ("cumin", "孜然", "调料", "kg"),
    "孜然籽": ("cumin", "孜然", "调料", "kg"),
    "十三香": ("thirteen_spices", "十三香", "调料", "kg"),
    "五香粉": ("five_spice", "五香粉", "调料", "kg"),
    "胡椒粉": ("pepper_powder", "胡椒粉", "调料", "kg"),
    "白胡椒粉": ("white_pepper", "白胡椒粉", "调料", "kg"),
    "黑胡椒": ("black_pepper", "黑胡椒", "调料", "kg"),
    "黑胡椒粉": ("black_pepper", "黑胡椒", "调料", "kg"),
    "辣椒粉": ("chili_powder", "辣椒粉", "调料", "kg"),
    "红辣椒粉": ("chili_powder", "辣椒粉", "调料", "kg"),
    "辣椒油": ("chili_oil", "辣椒油", "调料", "L"),
    "油泼辣子": ("chili_oil", "辣椒油", "调料", "L"),
    "咖喱块": ("curry", "咖喱", "调料", "kg"),
    "咖喱粉": ("curry", "咖喱", "调料", "kg"),
    "姜黄粉": ("turmeric", "姜黄粉", "调料", "kg"),
    "柠檬": ("lemon", "柠檬", "调料", "kg"),
    "柠檬汁": ("lemon_juice", "柠檬汁", "调料", "L"),
    "蜂蜜": ("honey", "蜂蜜", "调料", "kg"),
    "番茄沙司": ("ketchup", "番茄酱", "调料", "kg"),
    "南乳": ("red_bean_curd", "南乳", "调料", "kg"),
    "南腐乳": ("red_bean_curd", "南乳", "调料", "kg"),
    "腐乳": ("red_bean_curd", "南乳", "调料", "kg"),
    "柱侯酱": ("hoisin_sauce", "海鲜酱", "调料", "kg"),
    "小苏打": ("baking_soda", "小苏打", "调料", "kg"),
    "陈皮": ("dried_tangerine_peel", "陈皮", "调料", "kg"),
    "甘草": ("licorice", "甘草", "调料", "kg"),
    "白芷": ("angelica", "白芷", "调料", "kg"),
    "糯米粉": ("glutinous_rice_flour", "糯米粉", "调料", "kg"),
}

# 兜底分类规则（按顺序匹配，命中即返回）
FALLBACK_CATEGORY_RULES: list[tuple[tuple[str, ...], str]] = [
    (("酱油", "生抽", "老抽", "蚝油", "耗油", "醋", "盐", "糖", "料酒", "黄酒", "酱", "油",
      "花椒", "八角", "桂皮", "香叶", "孜然", "胡椒", "辣椒粉", "淀粉", "生粉", "味精",
      "鸡精", "香料", "咖喱", "豆豉", "腐乳", "南乳", "十三香", "五香", "茴香", "丁香",
      "苏打", "蜂蜜", "芥末"), "调料"),
    (("虾", "鱼", "蟹", "贝", "蛤", "鱿", "海参", "蚝"), "水产"),
    (("鸡", "猪", "牛", "羊", "鸭", "排骨", "五花", "培根", "火腿", "香肠", "腊肠", "肉"), "肉禽"),
    (("豆腐", "豆干", "腐竹", "豆浆", "豆皮"), "豆制品"),
    (("蛋",), "蛋奶"),
    (("奶", "奶油", "芝士", "奶酪", "酸奶"), "蛋奶"),
    (("米", "面", "馒头", "包子", "花卷", "饼", "面包", "粥", "粉条", "粉丝", "饺子"), "主食"),
    (("花生", "芝麻", "核桃", "腰果", "杏仁", "瓜子", "枣", "枸杞"), "坚果"),
    # 兜底：中文菜名的生僻食材绝大多数是蔬菜/菌菇
    (("菜", "蔬", "菇", "笋", "藕", "椒", "豆", "瓜", "萝卜", "芹", "葱", "姜"), "蔬菜"),
]

# 口味标签推断（词汇与 data/seed/recipes.json 保持一致：辛辣/油腻/生冷/清淡/海鲜）
SPICY_TOKENS = (
    "麻辣", "香辣", "辣子", "剁椒", "泡椒", "干锅", "水煮", "藤椒", "油泼", "红油",
    "咖喱", "黑胡椒", "白胡椒", "胡椒粉", "芥末", "辣白菜", "酸辣", "爆炒辣",
)
SPICY_INGREDIENTS = (
    "干辣椒", "小米椒", "小米辣", "辣椒粉", "红辣椒粉", "辣椒油", "豆瓣酱", "郫县豆瓣",
    "花椒", "青花椒", "尖椒", "螺丝椒", "朝天椒", "二荆条", "红辣椒", "辣椒",
)
OILY_TOKENS = (
    "油炸", "炸", "红烧", "油焖", "干锅", "干煸", "回锅", "肥肠", "五花", "猪蹄",
    "酥", "奶油", "芝士", "天妇罗", "煎炸",
)
COLD_TOKENS = ("凉拌", "沙拉", "冰镇", "冰块", "刺身", "生腌", "冷面", "凉皮", "冰")
LIGHT_TOKENS = ("清蒸", "清炒", "白灼", "清炖", "汆", "焯", "蒸", "粥", "汤")
SEAFOOD_TOKENS = ("虾", "鱼", "蟹", "贝", "蛤", "鱿", "海参", "蚝", "扇贝", "花甲")

ALLERGEN_RULES: list[tuple[tuple[str, ...], str]] = [
    (("虾",), "虾"),
    (("鱼", "蟹", "贝", "蛤", "鱿", "海参"), "鱼"),
    (("鸡蛋", "蛋"), "蛋"),
    (("豆腐", "豆干", "腐竹", "豆浆", "豆豉", "豆瓣"), "大豆"),
    (("花生", "花生油", "花生米"), "花生"),
    (("面粉", "面条", "挂面", "面包", "馒头", "包子", "花卷", "方便面"), "麸质"),
    (("牛奶", "奶油", "芝士", "奶酪", "酸奶", "黄油"), "奶"),
]

DURATION_BY_DIFFICULTY = {"简单": 15, "中等": 30, "困难": 50}


def _clean_stuff(raw: str) -> list[str]:
    """拆分并清洗 stuff 字段，去掉括号备注、工具与噪音词。"""
    results: list[str] = []
    for part in str(raw or "").replace("，", "、").replace(",", "、").split("、"):
        text = NOISE_PATTERN.sub("", part).strip()
        text = text.strip("：: 。.· ")
        if not text or text in NOISE_EXACT:
            continue
        if len(text) > 12:
            continue
        if text.endswith(TOOL_SUFFIX) and text not in INGREDIENT_MAP:
            continue
        results.append(text)
    return results


def _infer_category(name: str) -> str:
    for tokens, category in FALLBACK_CATEGORY_RULES:
        if any(token in name for token in tokens):
            return category
    return "其他"


# 复合食材名的兜底映射（"阿根廷红虾""虾滑""内酯豆腐"等）。
# 顺序敏感：更具体的词排前面，避免"鱿鱼"被"鱼"抢先命中。
SUBSTRING_RULES: list[tuple[str, tuple[str, str, str, str]]] = [
    ("虾", ("shrimp", "虾仁", "水产", "kg")),
    ("鱿", ("squid", "鱿鱼", "水产", "kg")),
    ("海参", ("sea_cucumber", "海参", "水产", "kg")),
    ("蟹", ("crab", "蟹", "水产", "kg")),
    ("贝", ("clam", "蛤蜊", "水产", "kg")),
    ("蛤", ("clam", "蛤蜊", "水产", "kg")),
    ("鱼", ("fish", "草鱼", "水产", "kg")),
    ("豆腐", ("tofu", "豆腐", "豆制品", "kg")),
    ("香菜", ("cilantro", "香菜", "蔬菜", "kg")),
    ("鸡蛋", ("egg", "鸡蛋", "蛋奶", "个")),
    ("蛋", ("egg", "鸡蛋", "蛋奶", "个")),
    ("香菇", ("mushroom", "香菇", "蔬菜", "kg")),
    ("蘑菇", ("mushroom", "香菇", "蔬菜", "kg")),
    ("口蘑", ("mushroom", "香菇", "蔬菜", "kg")),
    ("杏鲍菇", ("king_oyster", "杏鲍菇", "蔬菜", "kg")),
    ("金针菇", ("enoki", "金针菇", "蔬菜", "kg")),
    ("木耳", ("wood_ear", "木耳", "蔬菜", "kg")),
    ("火腿", ("ham", "火腿", "肉禽", "kg")),
    ("香肠", ("sausage", "香肠", "肉禽", "kg")),
    ("腊肠", ("sausage", "香肠", "肉禽", "kg")),
    ("五花", ("pork_belly", "五花肉", "肉禽", "kg")),
    ("排骨", ("ribs", "排骨", "肉禽", "kg")),
    ("牛腩", ("beef", "牛腩", "肉禽", "kg")),
    ("牛肉", ("beef", "牛腩", "肉禽", "kg")),
    ("羊肉", ("lamb", "羊肉", "肉禽", "kg")),
    ("大米", ("rice", "大米", "主食", "kg")),
    ("米饭", ("rice", "大米", "主食", "kg")),
    ("面条", ("noodles", "挂面", "主食", "kg")),
    ("面粉", ("flour", "面粉", "主食", "kg")),
]

# 命中这些字样的多为调料/制品而非主料，跳过子串规则以免误判（虾酱≠虾、鱼露≠鱼）
SEASONING_MARKERS = ("酱", "露", "油", "粉", "精", "汁", "干")


def _normalize_ingredient(name: str) -> tuple[str, str, str, str]:
    """返回 (key, 规范名, 类别, 单位)。未收录的按规则推断类别并生成稳定 key。"""
    if name in INGREDIENT_MAP:
        return INGREDIENT_MAP[name]
    if not any(marker in name for marker in SEASONING_MARKERS):
        for token, mapped in SUBSTRING_RULES:
            if token in name:
                return mapped
    category = _infer_category(name)
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
    return f"misc_{digest}", name, category, "kg"


def _taste_tags(entry: dict, ingredients: list[tuple[str, str]]) -> list[str]:
    name = str(entry.get("name") or "")
    tags = " ".join(str(t) for t in (entry.get("tags") or []))
    methods = str(entry.get("methods") or "")
    stuff_text = " ".join(text for text, _key in ingredients)
    blob = f"{name} {tags} {methods}"

    tags_out: list[str] = []
    if any(token in blob for token in SPICY_TOKENS) or any(
        token in stuff_text for token in SPICY_INGREDIENTS
    ):
        tags_out.append("辛辣")
    if any(token in blob for token in OILY_TOKENS):
        tags_out.append("油腻")
    if any(token in blob for token in COLD_TOKENS):
        tags_out.append("生冷")
    if any(token in blob for token in SEAFOOD_TOKENS) or any(
        token in stuff_text for token in SEAFOOD_TOKENS
    ):
        tags_out.append("海鲜")
    if not tags_out and any(token in blob for token in LIGHT_TOKENS):
        tags_out.append("清淡")
    return tags_out


def _allergens(ingredients: list[tuple[str, str]]) -> list[str]:
    names = " ".join(text for text, _key in ingredients)
    found: list[str] = []
    for tokens, label in ALLERGEN_RULES:
        if label in found:
            continue
        if any(token in names for token in tokens):
            found.append(label)
    return found


def _steps(entry: dict) -> list[str]:
    raw = entry.get("steps")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def _tags(entry: dict) -> list[str]:
    values: list[str] = []
    for item in entry.get("tags") or []:
        text = str(item).strip()
        if text and text not in values:
            values.append(text)
    for key in ("category", "methods"):
        text = str(entry.get(key) or "").strip()
        if text and text not in values:
            values.append(text)
    return values


def _source_url(entry: dict) -> str | None:
    url = entry.get("source")
    if url:
        return str(url)
    bv = entry.get("bv")
    if bv:
        return f"https://www.bilibili.com/video/{bv}"
    return None


def build_plan() -> tuple[list[dict], dict]:
    """解析源文件，产出待导入计划与统计信息（不写库）。"""
    with SOURCE_PATH.open(encoding="utf-8") as handle:
        raw = json.load(handle)

    stats = Counter()
    plan: list[dict] = []
    seen_names: set[str] = set()

    for entry in raw:
        stats["源数据总数"] += 1
        name = str(entry.get("name") or "").strip()
        if not name or name in seen_names:
            stats["重名或无名跳过"] += 1
            continue
        category = str(entry.get("category") or "").strip()
        if category in SKIP_CATEGORIES:
            stats[f"排除非正餐({category})"] += 1
            continue
        if any(token in name for token in NOT_DISH_TOKENS):
            stats["排除非菜品条目"] += 1
            continue

        cleaned = _clean_stuff(entry.get("stuff"))
        if not cleaned:
            stats["食材为空跳过"] += 1
            continue

        pairs: list[tuple[str, str]] = []
        for text in cleaned:
            _key, canonical, _cat, _unit = _normalize_ingredient(text)
            if (canonical, _key) not in pairs:
                pairs.append((canonical, _key))
        if not pairs:
            stats["食材为空跳过"] += 1
            continue

        seen_names.add(name)
        plan.append(
            {
                "name": name,
                "servings": 2,
                "duration_minutes": DURATION_BY_DIFFICULTY.get(
                    str(entry.get("difficulty") or "").strip(), 30
                ),
                "tags": _tags(entry),
                "taste_tags": _taste_tags(entry, pairs),
                "allergens": _allergens(pairs),
                "steps": _steps(entry),
                "source_url": _source_url(entry),
                "ingredients": pairs,
            }
        )
        stats["进入导入计划"] += 1

    return plan, dict(stats)


def apply_plan(plan: list[dict]) -> dict:
    """把计划写入数据库。同名菜谱跳过，绝不修改既有菜谱。"""
    from app.db.models import Ingredient, Recipe, RecipeIngredient
    from app.db.session import SessionLocal

    session = SessionLocal()
    created_recipes = 0
    created_ingredients = 0
    skipped_existing = 0
    role_counter: Counter[str] = Counter()

    try:
        by_key = {ing.key: ing for ing in session.scalars(select(Ingredient)).all()}
        by_name = {ing.name: ing for ing in by_key.values()}

        existing_names = {
            name for (name,) in session.execute(select(Recipe.name)).all()
        }

        for item in plan:
            if item["name"] in existing_names:
                skipped_existing += 1
                continue

            facts: list[IngredientFact] = []
            links: list[tuple[Ingredient, str, str]] = []
            for canonical, key in item["ingredients"]:
                ingredient = by_key.get(key) or by_name.get(canonical)
                if ingredient is None:
                    _k, _n, category, unit = _normalize_ingredient(canonical)
                    ingredient = Ingredient(
                        key=key, name=canonical, category=category, default_unit=unit
                    )
                    session.add(ingredient)
                    session.flush()
                    by_key[key] = ingredient
                    by_name[canonical] = ingredient
                    created_ingredients += 1
                links.append((ingredient, unit_of(ingredient), canonical))
                facts.append(
                    IngredientFact(
                        key=ingredient.key,
                        name=ingredient.name,
                        category=ingredient.category,
                        quantity=Decimal("1"),
                        unit="",
                        optional=False,
                    )
                )

            classified = classify_recipe(item["name"], item["tags"], facts)
            recipe = Recipe(
                name=item["name"],
                servings=item["servings"],
                duration_minutes=item["duration_minutes"],
                kcal_per_serving=None,
                estimated_cost_cents=None,
                tags=item["tags"],
                taste_tags=item["taste_tags"],
                allergens=item["allergens"],
                steps=item["steps"],
                source_name=SOURCE_NAME,
                source_url=item["source_url"],
                license_note=LICENSE_NOTE,
                meal_role=classified.meal_role,
                staple_type=classified.staple_type,
                protein_sources=classified.protein_sources,
                vegetable_keys=classified.vegetable_keys,
                portion_confidence=classified.portion_confidence,
            )
            session.add(recipe)
            session.flush()

            for ingredient, unit, _canonical in links:
                session.add(
                    RecipeIngredient(
                        recipe_id=recipe.id,
                        ingredient_id=ingredient.id,
                        quantity=Decimal("1"),
                        unit=unit,
                        optional=False,
                    )
                )
            created_recipes += 1
            role_counter[classified.meal_role or "未分类"] += 1

        session.commit()
    finally:
        session.close()

    return {
        "新建菜谱": created_recipes,
        "新建食材": created_ingredients,
        "重名跳过": skipped_existing,
        "角色分布": dict(role_counter),
    }


def unit_of(ingredient) -> str:
    """源数据无用量，单位留空 -> portion_confidence 落为 servings_only。"""
    return ""


def reset_lazy_recipes() -> dict:
    """清掉本脚本此前导入的菜谱，便于修正规则后重跑。绝不触碰种子菜谱。"""
    from app.db.models import Ingredient, Recipe, RecipeIngredient
    from app.db.session import SessionLocal
    from sqlalchemy import delete

    session = SessionLocal()
    try:
        ids = [
            row[0]
            for row in session.execute(
                select(Recipe.id).where(Recipe.source_name == SOURCE_NAME)
            ).all()
        ]
        removed_links = 0
        if ids:
            removed_links = session.execute(
                delete(RecipeIngredient).where(RecipeIngredient.recipe_id.in_(ids))
            ).rowcount or 0
            session.execute(delete(Recipe).where(Recipe.id.in_(ids)))
        # 清掉无人引用的临时食材（misc_*），保留种子与常用食材
        orphan = session.execute(
            select(Ingredient.id).where(
                Ingredient.key.like("misc_%"),
                ~Ingredient.id.in_(select(RecipeIngredient.ingredient_id)),
            )
        ).all()
        orphan_ids = [row[0] for row in orphan]
        if orphan_ids:
            session.execute(delete(Ingredient).where(Ingredient.id.in_(orphan_ids)))
        session.commit()
    finally:
        session.close()
    return {"清理菜谱": len(ids), "清理配方行": removed_links, "清理临时食材": len(orphan_ids)}


def main() -> None:
    parser = argparse.ArgumentParser(description="导入懒人宝典菜谱到 Agent 菜谱库")
    parser.add_argument("--apply", action="store_true", help="真正写入数据库（默认仅统计）")
    parser.add_argument("--reset", action="store_true", help="先清理本脚本此前导入的菜谱")
    args = parser.parse_args()

    if args.reset:
        print("=== 清理旧导入 ===")
        for key, value in reset_lazy_recipes().items():
            print(f"  {key}: {value}")

    plan, stats = build_plan()
    print("=== 解析统计 ===")
    for key, value in sorted(stats.items()):
        print(f"  {key}: {value}")

    taste_counter = Counter(tag for item in plan for tag in item["taste_tags"])
    allergen_counter = Counter(tag for item in plan for tag in item["allergens"])
    print("  口味标签:", dict(taste_counter))
    print("  过敏原:", dict(allergen_counter))

    # 预演分类：只有 meal_role 非空的菜才会进入自动整餐组合
    role_counter: Counter[str] = Counter()
    unclassified_sample: list[str] = []
    for item in plan:
        facts = [
            IngredientFact(
                key=key,
                name=canonical,
                category=_normalize_ingredient(canonical)[2],
                quantity=Decimal("1"),
                unit="",
                optional=False,
            )
            for canonical, key in item["ingredients"]
        ]
        classified = classify_recipe(item["name"], item["tags"], facts)
        role_counter[classified.meal_role or "未分类"] += 1
        if not classified.meal_role and len(unclassified_sample) < 12:
            unclassified_sample.append(f"{item['name']}({','.join(c for c, _ in item['ingredients'])})")
    composable = sum(
        count for role, count in role_counter.items() if role in
        {"protein_dish", "vegetable_dish", "mixed_dish", "soup", "staple_side", "complete_meal"}
    )
    print(f"  可进配餐(角色非空): {composable} / {len(plan)}")
    print("  角色分布:", dict(role_counter))
    if unclassified_sample:
        print("  未分类样例:", unclassified_sample)

    if not args.apply:
        print("\n（仅统计，未写库。确认无误后加 --apply 执行导入）")
        return

    print("\n=== 导入结果 ===")
    for key, value in apply_plan(plan).items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
