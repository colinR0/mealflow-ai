"""Create nine original, multi-format recipes for loader verification."""

from __future__ import annotations

import hashlib
import json
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether

ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "output/pdf/recipe-loading-v1"
TEXT_DIR = ROOT / "output/recipe-loading-v1"
SOURCE = "MealFlow 原创多格式加载测试菜谱"
LICENSE = "本项目自制测试内容，可用于本项目开发、评测与展示；非营养或医疗建议。"

# Each recipe is a standalone parent document. Quantities are for its stated base yield.
RECIPES = [
    dict(id="rload-001", fmt="pdf", name="姜葱清蒸鲈鱼", category="水产", servings=3, prep=18, cook=12,
         equipment="蒸锅、耐热鱼盘、厨房纸、筷子", allergens="鱼、大豆（生抽）；调料品牌可能含其他过敏原，请核对包装",
         ingredients=["鲈鱼 1 条，约 600 g，去鳞去内脏", "生姜 25 g，切丝", "小葱 30 g，葱白与葱绿分开", "料酒 15 mL", "生抽 20 mL", "清水 20 mL", "食用油 15 mL"],
         steps=["检查鱼腹和鳃部，除去残留血块，用流动水冲洗后以厨房纸擦干；鱼身两侧各划两刀，方便受热。", "盘底铺一半姜丝与葱白，放上鱼；鱼腹塞入少量姜葱，鱼身铺余下姜丝，均匀淋料酒。静置 8 分钟。", "蒸锅加足水，大火烧至持续沸腾后再放入鱼盘；盖紧锅盖，从重新上汽时开始计时 10 至 12 分钟。", "关火后静置 2 分钟，用筷子检查鱼背最厚处：鱼肉应不透明、易与骨分离；若仍半透明，继续蒸至熟透。", "端出鱼盘，小心倒掉盘中蒸出的汁水和旧姜葱；这一步可减轻腥味，注意防烫。", "把葱绿铺在鱼身上；将生抽与清水混合，沿鱼身和盘边淋入。", "小锅将食用油加热至流动性明显增强但未冒烟，均匀淋在葱丝上；立即上桌。"],
         finish="鱼背最厚处完全不透明、鱼肉轻拨即散；没有生鱼味。", substitutions="可用同重量已处理的其他整鱼替换，但鱼体厚度不同，蒸制时间须重新核对；无大豆需求时改用经核验的不含大豆调味品，不能直接宣称成品无过敏原。"),
    dict(id="rload-002", fmt="pdf", name="电饭煲菌菇鸡腿饭", category="主食", servings=4, prep=25, cook=35,
         equipment="电饭煲、炒锅、量杯、砧板", allergens="大豆（生抽、老抽）；菌菇及调料请按实际包装核对",
         ingredients=["去骨鸡腿肉 400 g，切 2 cm 块", "大米 300 g", "鲜香菇 120 g，切片", "胡萝卜 100 g，切丁", "洋葱 80 g，切丁", "生姜 15 g，切末", "生抽 25 mL", "老抽 5 mL", "食用油 15 mL", "清水 约 360 mL 起，按电饭煲 300 g 米刻度调整"],
         steps=["淘洗大米两遍，沥干后放入内胆；先不要加水，以免后续配料使水位判断失准。", "鸡腿肉切块并擦去多余水分；香菇、胡萝卜、洋葱分别切好，生熟食材使用分开的刀板。", "炒锅加油和姜末，中火炒出香气；加入鸡腿肉翻炒至表面全部变色。", "加入洋葱、胡萝卜和香菇，继续翻炒约 3 分钟；加入生抽、老抽拌匀，关火。", "将锅中食材连同汁液倒在米上，轻轻铺平；加清水至对应米量刻度，汁液较多时略减水，不要超过内胆最高水位。", "启动标准煮饭程序；程序结束后焖 10 分钟，再开盖检查鸡肉最厚块已完全熟透，若未熟透则继续加热。", "用饭勺从底部翻拌，让米饭和配料均匀分布；如饭粒偏湿，开盖散汽 3 分钟后装盘。"],
         finish="米饭中心无硬芯，鸡肉内部完全熟透，内胆底部没有大量游离水。", substitutions="香菇可换杏鲍菇；不同电饭煲的刻度和程序不同，水量以设备说明及米饭状态调整；去骨鸡胸替换鸡腿时需防止过度加热。"),
    dict(id="rload-003", fmt="pdf", name="冬瓜玉米排骨汤", category="汤", servings=4, prep=25, cook=75,
         equipment="汤锅、漏勺、刀板", allergens="未使用常见过敏原原料；仍须核对实际排骨和调料的交叉接触信息",
         ingredients=["猪排骨 500 g", "冬瓜 400 g，去皮去瓤切块", "甜玉米 1 根，约 250 g，切段", "生姜 20 g，切片", "小葱 15 g", "清水 1.8 L，另备焯水用水", "食盐 5 g，可按口味调整"],
         steps=["排骨剁成适合入口的块，检查碎骨并冲洗；冬瓜切 3 cm 块，玉米切段。", "排骨冷水下锅，水量没过排骨；大火烧开后继续煮 2 分钟，撇去浮沫。", "捞出排骨，用温水冲净表面浮沫；倒掉焯水并清洗锅具。", "汤锅加入排骨、生姜和 1.8 L 清水，大火煮沸，转小火保持轻微翻滚，盖锅盖炖 45 分钟。", "加入玉米段，继续炖 15 分钟；期间检查水量，必要时补少量热水。", "加入冬瓜，再炖约 12 至 15 分钟，直到冬瓜中心半透明、排骨肉易从骨边剥离。", "加入食盐搅匀，尝味后再调整；撒葱花，关火稍放凉后盛出，提醒食用者留意骨头。"],
         finish="排骨肉熟透且易剥离，冬瓜中心软透，汤中无生肉味。", substitutions="玉米可换白萝卜 250 g，但加入时间需按切块大小调整；不建议把冬瓜从开头炖到结束，以免散碎。"),
    dict(id="rload-004", fmt="md", name="虾仁豆腐蒸蛋", category="蒸菜", servings=3, prep=18, cook=16,
         equipment="蒸锅、深口耐热碗、滤网", allergens="虾、蛋、大豆；生抽也含大豆，调味品须核对包装",
         ingredients=["去壳去肠线虾仁 150 g", "鸡蛋 3 个", "嫩豆腐 200 g", "温水 240 mL", "生抽 10 mL", "食盐 2 g", "香油 5 mL，可选", "小葱 10 g，可选"],
         steps=["虾仁检查并去除残余肠线，洗净后沥干；嫩豆腐切约 2 cm 小块，平铺在耐热碗底。", "鸡蛋打散，加食盐和温水轻轻搅匀，不要高速打出大量泡沫。", "蛋液过筛一次，缓缓倒入豆腐碗中；撇去表面泡沫，给碗盖耐热盖或留缝保鲜膜。", "蒸锅水烧开后转中火，放入碗蒸约 8 分钟，观察蛋液外圈基本凝固。", "打开锅盖小心摆入虾仁，再蒸 6 至 8 分钟；避免蒸汽烫伤。", "检查中心蛋液不再流动、虾仁完全变色且熟透；取出后淋生抽和可选香油，撒小葱。"],
         finish="蛋羹中心凝固，虾仁整体不透明且熟透。", substitutions="虾过敏者不应仅把虾取出后继续食用同一碗蛋羹；可重新制作无虾版本，并核对器具交叉接触。"),
    dict(id="rload-005", fmt="md", name="西兰花蒜香牛肉", category="荤菜", servings=3, prep=22, cook=13,
         equipment="炒锅、漏勺、刀板", allergens="大豆（生抽）；蚝油若使用则可能含贝类，本配方未使用",
         ingredients=["牛里脊 300 g，逆纹切薄片", "西兰花 350 g，切小朵", "大蒜 25 g，切末", "生抽 20 mL", "淀粉 8 g", "食用油 20 mL", "清水 40 mL", "食盐 2 g"],
         steps=["牛肉逆纹切片，用生抽和淀粉拌匀，静置 10 分钟；生肉用具与蔬菜用具分开。", "西兰花切成大小接近的小朵，用清水冲洗花球缝隙，沥干。", "锅中烧水，加入西兰花焯约 90 秒，捞出沥干；后续仍需炒熟。", "炒锅加一半食用油，中大火分两批快炒牛肉，表面变色后先盛出，避免一次入锅过多导致出水。", "加入余油和蒜末，炒出香味后放入西兰花，加清水翻炒约 2 分钟。", "倒回牛肉，翻炒至牛肉完全熟透；撒盐拌匀，锅中水分收至少量即出锅。"],
         finish="牛肉内部无生肉色，西兰花茎部熟而仍能保持形状。", substitutions="可用菜花替换西兰花，焯煮和翻炒时间需按菜梗厚度调整；不要用蚝油替代生抽而不重新核对过敏原。"),
    dict(id="rload-006", fmt="md", name="番茄扁豆意面", category="主食", servings=3, prep=18, cook=25,
         equipment="汤锅、深炒锅、漏勺", allergens="小麦（常规意面）；番茄制品和调料须核对包装",
         ingredients=["干意大利面 240 g", "熟扁豆 240 g，沥干", "番茄 350 g，切丁", "洋葱 100 g，切丁", "大蒜 15 g，切末", "橄榄油 20 mL", "清水 120 mL", "食盐 4 g", "黑胡椒 1 g"],
         steps=["确认使用的是已煮熟、可直接食用的扁豆；若使用干扁豆，必须先按包装说明单独煮熟，不能直接加入酱汁。", "番茄、洋葱切丁，大蒜切末；大锅烧开足量水，按意面包装时间煮面并留出半杯面汤。", "另一锅加入橄榄油，中火炒软洋葱约 4 分钟，再加入蒜末炒约 30 秒。", "加入番茄丁和清水，煮至番茄软化、汁液稍浓；用锅铲轻压较大番茄块。", "加入熟扁豆，小火煮约 5 分钟，确保整体热透；用食盐和黑胡椒调味。", "将煮好的意面沥水后加入酱锅，翻拌 1 至 2 分钟；过干时分次加少量预留面汤。"],
         finish="面条无硬芯，扁豆完全热透，酱汁均匀附着在面条上。", substitutions="可换经核验的无小麦意面，但仍应按其包装单独确认烹煮时间和过敏原；罐装熟扁豆应先沥液并检查配料表。"),
    dict(id="rload-007", fmt="txt", name="香菇青菜豆腐汤", category="汤", servings=4, prep=15, cook=18,
         equipment="汤锅、刀板、汤勺", allergens="大豆（豆腐）；调味品须核对包装",
         ingredients=["北豆腐 300 g，切 2 cm 块", "鲜香菇 150 g，切片", "小青菜 250 g，洗净切段", "生姜 10 g，切丝", "清水 1.2 L", "食用油 10 mL", "食盐 4 g"],
         steps=["青菜逐叶检查并洗净泥沙，香菇去蒂后切片；豆腐切块并轻拿，避免破碎。", "汤锅加油和姜丝，中火炒出香气后加入香菇片，翻炒约 3 分钟至香菇变软。", "加入 1.2 L 清水，大火煮沸；转中火煮 5 分钟，使香菇香味进入汤中。", "沿锅边缓缓放入豆腐，保持轻微沸腾再煮约 5 分钟，尽量少搅动。", "加入青菜梗部煮 2 分钟，再加入叶部煮 1 至 2 分钟，直到菜梗熟软。", "加入食盐搅匀，尝味调整；关火后稍放凉，再分碗盛出。"],
         finish="香菇、青菜梗均已熟软，豆腐中心热透。", substitutions="北豆腐可换嫩豆腐，但下锅后应减少搅动；对大豆过敏者需要另选菜谱，不应简单挑出豆腐。"),
    dict(id="rload-008", fmt="txt", name="土豆胡萝卜炖鸡", category="荤菜", servings=4, prep=25, cook=45,
         equipment="深炖锅、锅盖、刀板", allergens="大豆（生抽）；实际调味品须核对包装",
         ingredients=["去骨鸡腿肉 500 g，切块", "土豆 350 g，切 3 cm 块", "胡萝卜 250 g，切滚刀块", "洋葱 100 g，切块", "生姜 15 g，切片", "生抽 20 mL", "食用油 20 mL", "清水 500 mL", "食盐 3 g"],
         steps=["鸡肉切块并擦干，土豆去皮切块后浸在清水中防止变色；生熟食材分别使用刀板。", "炖锅加油，中火煸香姜片和洋葱约 3 分钟。", "加入鸡块，翻炒至各面变色，再加入生抽炒匀。", "加入胡萝卜和 500 mL 清水，煮沸后转小火盖锅炖 15 分钟。", "沥干土豆并加入锅中，继续炖约 18 至 22 分钟；中途轻翻一次并检查水量。", "检查鸡肉最厚处完全熟透、土豆中心能被筷子轻松刺穿；若仍偏硬，继续加盖炖煮。", "开盖按口味加盐，短时收汁至酱汁能薄薄裹住食材，关火盛出。"],
         finish="鸡肉完全熟透，土豆与胡萝卜无硬芯。", substitutions="可用等量南瓜替换部分土豆，但南瓜较易散，宜在最后约 12 分钟加入；鸡胸肉替换鸡腿时需留意口感变柴。"),
    dict(id="rload-009", fmt="txt", name="南瓜小米粥", category="早餐", servings=4, prep=12, cook=35,
         equipment="厚底汤锅、长柄勺", allergens="本配方未加入常见过敏原；小米加工包装及器具交叉接触信息仍需核对",
         ingredients=["小米 160 g", "南瓜 300 g，去皮去瓤切 2 cm 块", "清水 1.4 L", "食盐 1 g，可选"],
         steps=["小米检查是否有杂质，用清水快速淘洗两遍后沥干；南瓜去皮去瓤并切成大小接近的小块。", "厚底锅加入清水，烧开后下小米，搅动锅底防止结块。", "再次煮沸后转小火，保持轻微咕嘟并留一道锅盖缝，煮约 12 分钟；期间搅动两次。", "加入南瓜块，小火再煮约 18 至 22 分钟；若粥过稠，少量分次加入热水。", "用勺压碎一小部分南瓜，让粥自然变稠；继续煮至小米完全软化，关火静置 5 分钟。", "搅匀并检查温度后盛碗；可选食盐只用于轻微调味，不依赖糖或蜂蜜增加甜味。"],
         finish="小米粒软而无硬芯，南瓜块可用勺轻压成泥，粥体均匀。", substitutions="可用同重量去皮红薯替换南瓜，但烹煮时间须按块大小和实际软化程度调整；水量可按喜欢的稠度在煮制中逐次调整。"),
]


def lines(recipe: dict) -> list[str]:
    return [
        f"菜谱ID：{recipe['id']}", "版本：1", f"菜名：{recipe['name']}",
        f"类别：{recipe['category']}", f"基准份数：{recipe['servings']} 人份",
        f"准备时间：{recipe['prep']} 分钟", f"烹饪时间：{recipe['cook']} 分钟",
        f"预计总时间：{recipe['prep'] + recipe['cook']} 分钟", f"设备：{recipe['equipment']}",
        f"过敏原与核对：{recipe['allergens']}", "", "食材（基于上述份数）",
        *[f"- {item}" for item in recipe["ingredients"]], "", "做法步骤",
        *[f"{i}. {step}" for i, step in enumerate(recipe["steps"], 1)], "",
        f"完成判断：{recipe['finish']}", f"可选替换及限制：{recipe['substitutions']}",
        f"来源：{SOURCE}", f"使用说明：{LICENSE}",
    ]


def render_pdf(path: Path, recipe: dict) -> None:
    pdfmetrics.registerFont(TTFont("SimHeiRecipe", r"C:\Windows\Fonts\simhei.ttf"))
    styles = getSampleStyleSheet()
    title = ParagraphStyle("RecipeTitle", parent=styles["Title"], fontName="SimHeiRecipe",
                           fontSize=18, leading=25, alignment=TA_CENTER, textColor=colors.HexColor("#243f5b"))
    body = ParagraphStyle("RecipeBody", parent=styles["BodyText"], fontName="SimHeiRecipe",
                          fontSize=9.5, leading=14, spaceAfter=3)
    heading = ParagraphStyle("RecipeHeading", parent=body, fontSize=11, leading=17,
                             textColor=colors.HexColor("#245d74"), spaceBefore=7)
    story = [Paragraph(escape(recipe["name"]), title), Spacer(1, 6)]
    for line in lines(recipe):
        if not line:
            story.append(Spacer(1, 3))
        elif line in {"食材（基于上述份数）", "做法步骤"}:
            story.append(Paragraph(escape(line), heading))
        else:
            story.append(Paragraph(escape(line), body))
    SimpleDocTemplate(str(path), pagesize=A4, rightMargin=43, leftMargin=43,
                      topMargin=38, bottomMargin=38, title=recipe["name"]).build(story)


def main() -> None:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for recipe in RECIPES:
        suffix = "pdf" if recipe["fmt"] == "pdf" else "md" if recipe["fmt"] == "md" else "txt"
        directory = PDF_DIR if suffix == "pdf" else TEXT_DIR / suffix
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{recipe['id']}.{suffix}"
        if suffix == "pdf":
            render_pdf(path, recipe)
        elif suffix == "md":
            body = ["## 食材（基于上述份数）" if line == "食材（基于上述份数）"
                    else "## 做法步骤" if line == "做法步骤" else line for line in lines(recipe)]
            content = "# " + recipe["name"] + "\n\n" + "\n".join(body) + "\n"
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text("\n".join(lines(recipe)) + "\n", encoding="utf-8")
        manifest.append({"id": recipe["id"], "name": recipe["name"], "format": suffix,
                         "path": path.relative_to(ROOT).as_posix(),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "source": SOURCE, "version": 1})
    (TEXT_DIR / "manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "recipes": manifest}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
