from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkspaceKind(StrEnum):
    ORGANIZATION = "organization"
    PERSONAL = "personal"


class AgentRunStatus(StrEnum):
    PARSING = "PARSING"
    RUNNING_TOOLS = "RUNNING_TOOLS"
    BUILDING_OPTIONS = "BUILDING_OPTIONS"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    PUBLISHED = "PUBLISHED"
    FALLBACK = "FALLBACK"
    FAILED = "FAILED"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)  # 记录创建时间（UTC）
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )  # 最近一次更新时间（UTC）


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 工作区唯一 ID
    name: Mapped[str] = mapped_column(String(120))  # 工作区名称，例如“张三的家”
    kind: Mapped[str] = mapped_column(String(32), default=WorkspaceKind.ORGANIZATION.value)  # 工作区类型：组织或个人


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 用户唯一 ID
    email: Mapped[str | None] = mapped_column(String(160), unique=True, index=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(11), unique=True, index=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))  # 加密后的密码，绝不保存明文
    display_name: Mapped[str] = mapped_column(String(80))  # 页面显示的用户名
    # 家庭档案扩展:性别/常驻或访客/忌口(自由文本)/备注(特殊事件)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)  # 成员性别，可为空
    residency: Mapped[str] = mapped_column(String(10), default="resident")  # 常住成员或访客标识
    taboo: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 自由文本忌口说明
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)  # 成员备注，例如特殊情况


class WorkspaceMember(TimestampMixin, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_membership"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 成员关系记录 ID
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)  # 所属工作区 ID
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)  # 对应用户 ID
    # 统一家庭用户；权限由有效成员关系决定。
    role: Mapped[str] = mapped_column(String(32), default="user")
    status: Mapped[str] = mapped_column(String(32), default="active")  # 成员状态，例如 active


class HouseholdPantryConfig(TimestampMixin, Base):
    """工作区级常备料覆盖；默认项不必逐条落库。"""

    __tablename__ = "household_pantry_configs"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)  # 配置所属工作区 ID
    use_defaults: Mapped[bool] = mapped_column(Boolean, default=True)  # 是否启用系统默认常备料
    disabled_default_keys: Mapped[list[str]] = mapped_column(JSON, default=list)  # 被关闭的默认常备料 key 列表
    custom_keys: Mapped[list[str]] = mapped_column(JSON, default=list)  # 用户额外添加的常备料 key 列表


class DietaryRestriction(TimestampMixin, Base):
    __tablename__ = "dietary_restrictions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 限制记录唯一 ID
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)  # 所属工作区 ID
    # 家庭模式:user_id 为空表示全家共享限制,否则为成员个人忌口/过敏
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)  # 对应成员 ID；为空代表全家限制
    type: Mapped[str] = mapped_column(String(32))  # 限制类型，例如 allergy/dislike
    ingredient_key: Mapped[str] = mapped_column(String(80))  # 被限制食材的唯一 key


class MemberFoodPreference(TimestampMixin, Base):
    """成员长期食物偏好；菜品偏好与食材忌口分开建模。"""

    __tablename__ = "member_food_preferences"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            "target_type",
            "target_key",
            name="uq_member_food_preference_target",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 偏好记录唯一 ID
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)  # 所属工作区 ID
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)  # 记录偏好的成员 ID
    target_type: Mapped[str] = mapped_column(String(16))  # 偏好对象类型：dish（菜）或 ingredient（食材）
    target_key: Mapped[str] = mapped_column(String(160))  # 偏好对象的稳定标识
    target_name: Mapped[str] = mapped_column(String(160))  # 偏好对象的显示名称
    preference: Mapped[str] = mapped_column(String(16))  # 喜欢 like 或不喜欢 dislike
    source: Mapped[str] = mapped_column(String(24), default="profile")  # 偏好录入来源，例如 profile/conversation


class MemberEvent(TimestampMixin, Base):
    """家庭成员的特殊事件(例假/感冒等),带生效日期,到期自动失效。"""

    __tablename__ = "member_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 事件记录唯一 ID
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)  # 所属工作区 ID
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)  # 发生事件的成员 ID
    event_type: Mapped[str] = mapped_column(String(32))  # 事件类型，例如感冒、例假
    constraint_tags: Mapped[list[str]] = mapped_column(JSON, default=list)  # 事件期间的饮食限制标签
    start_date: Mapped[date] = mapped_column(Date)  # 事件生效开始日期
    end_date: Mapped[date] = mapped_column(Date)  # 事件生效结束日期
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 事件补充说明


class Site(TimestampMixin, Base):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 用餐地点记录唯一 ID
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)  # 所属工作区 ID
    name: Mapped[str] = mapped_column(String(120))  # 用餐地点名称
    service_capacity: Mapped[int] = mapped_column(Integer, default=0)  # 可接待或服务人数上限


class MealService(TimestampMixin, Base):
    __tablename__ = "meal_services"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 餐次记录唯一 ID
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), index=True)  # 用餐地点 ID
    name: Mapped[str] = mapped_column(String(40), default="午餐")  # 餐次名称，如早餐或午餐
    service_date: Mapped[date] = mapped_column(Date)  # 本次供餐日期
    expected_diners: Mapped[int] = mapped_column(Integer)  # 预计用餐人数
    budget_limit_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 本餐预算上限（分），可为空
    status: Mapped[str] = mapped_column(String(32), default="draft")  # 餐次状态，例如 draft


class Ingredient(TimestampMixin, Base):
    __tablename__ = "ingredients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 食材唯一 ID
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)  # 程序使用的稳定食材 key
    name: Mapped[str] = mapped_column(String(80))  # 食材展示名称
    category: Mapped[str] = mapped_column(String(60))  # 食材分类
    default_unit: Mapped[str] = mapped_column(String(20))  # 默认计量单位
    # 采购成本按市区大型超市参考价计算,金额输出必须携带来源标注
    reference_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)  # 参考单价
    price_source: Mapped[str] = mapped_column(
        String(120), default="市区大型超市公开参考价"
    )  # 参考价格来源
    price_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 参考价格最近更新时间


class InventoryItem(TimestampMixin, Base):
    __tablename__ = "inventory_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 库存记录唯一 ID
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)  # 库存所属工作区
    ingredient_id: Mapped[str] = mapped_column(ForeignKey("ingredients.id"), index=True)  # 对应食材 ID
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))  # 当前库存数量
    unit: Mapped[str] = mapped_column(String(20))  # 库存计量单位
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)  # 到期日，未知可为空


class Recipe(TimestampMixin, Base):
    __tablename__ = "recipes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 菜谱唯一 ID
    name: Mapped[str] = mapped_column(String(120), index=True)  # 菜谱名称
    servings: Mapped[int] = mapped_column(Integer)  # 原始菜谱适用人数
    duration_minutes: Mapped[int] = mapped_column(Integer)  # 预计制作时长（分钟）
    kcal_per_serving: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 每人份热量，可为空
    estimated_cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 估算成本（分），可为空
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)  # 菜谱普通标签列表
    # 口味维度 tags(辛辣/油腻/生冷/清淡/海鲜/温补),与成员约束做冲突检测
    taste_tags: Mapped[list[str]] = mapped_column(JSON, default=list)  # 口味或饮食特征标签
    allergens: Mapped[list[str]] = mapped_column(JSON, default=list)  # 菜谱可能含有的过敏原
    steps: Mapped[list[str]] = mapped_column(JSON, default=list)  # 制作步骤列表
    source_name: Mapped[str] = mapped_column(String(160), default="自制演示数据")  # 菜谱来源名称
    source_url: Mapped[str | None] = mapped_column(String(300), nullable=True)  # 菜谱来源链接，可为空
    license_note: Mapped[str] = mapped_column(
        String(200), default="自制演示数据，仅用于演示，可再分发"
    )  # 菜谱使用或授权说明
    meal_role: Mapped[str] = mapped_column(String(32), default="", server_default="")  # 菜在整餐中的角色
    staple_type: Mapped[str] = mapped_column(String(16), default="无", server_default="无")  # 主食类型
    protein_sources: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")  # 主要蛋白质来源列表
    vegetable_keys: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")  # 蔬菜食材 key 列表
    portion_confidence: Mapped[str] = mapped_column(
        String(32), default="servings_only", server_default="servings_only"
    )  # 份量数据可信程度


class RecipeIngredient(TimestampMixin, Base):
    __tablename__ = "recipe_ingredients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 配方明细唯一 ID
    recipe_id: Mapped[str] = mapped_column(ForeignKey("recipes.id"), index=True)  # 所属菜谱 ID
    ingredient_id: Mapped[str] = mapped_column(ForeignKey("ingredients.id"), index=True)  # 使用食材 ID
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))  # 此菜谱所需食材数量
    unit: Mapped[str] = mapped_column(String(20))  # 此用量的单位
    optional: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否为可选食材


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 一次 Agent 运行唯一 ID
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)  # 运行所属工作区
    meal_service_id: Mapped[str] = mapped_column(ForeignKey("meal_services.id"), index=True)  # 对应餐次 ID
    request_text: Mapped[str] = mapped_column(Text)  # 用户原始需求文本
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)  # 本轮提取和使用的约束快照
    state: Mapped[dict] = mapped_column(JSON, default=dict)  # Agent 运行过程状态快照
    status: Mapped[str] = mapped_column(String(32), default=AgentRunStatus.PARSING.value)  # 当前运行状态
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 模型供应商，可为空
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)  # 所用模型名称，可为空


class ToolTrace(TimestampMixin, Base):
    __tablename__ = "tool_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 工具调用记录唯一 ID
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)  # 所属 Agent 运行 ID
    tool_name: Mapped[str] = mapped_column(String(100))  # 被调用的工具名称
    status: Mapped[str] = mapped_column(String(32))  # 调用结果状态
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)  # 调用耗时（毫秒）
    input_summary: Mapped[str] = mapped_column(String(500), default="")  # 工具输入的可读摘要
    output_summary: Mapped[str] = mapped_column(String(500), default="")  # 工具输出的可读摘要
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)  # 失败错误码，可为空


class MealPlan(TimestampMixin, Base):
    __tablename__ = "meal_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 菜单方案唯一 ID
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)  # 所属工作区
    meal_service_id: Mapped[str] = mapped_column(ForeignKey("meal_services.id"), index=True)  # 对应餐次 ID
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)  # 生成方案的 Agent 运行 ID
    strategy: Mapped[str] = mapped_column(String(40))  # 方案采用的策略名称
    items: Mapped[list[dict]] = mapped_column(JSON, default=list)  # 菜单中的菜品明细
    procurement_list: Mapped[list[dict]] = mapped_column(JSON, default=list)  # 建议采购清单
    estimated_cost_cents: Mapped[int] = mapped_column(Integer, default=0)  # 方案估算总成本（分）
    # 数据字典：保存生成时的解释快照（约束与评分拆解），发布后不再变化
    constraints_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)  # 生成时的约束和评分快照
    status: Mapped[str] = mapped_column(String(32), default="draft")  # 菜单状态，例如 draft 或 published
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 发布时间，可为空
    # api.md：发布接口必须使用 Idempotency-Key，同键重放返回同一结果
    publish_idempotency_key: Mapped[str | None] = mapped_column(
        String(80), unique=True, nullable=True
    )  # 发布幂等键，同键重复请求不重复发布


class AgentChatSession(TimestampMixin, Base):
    """对话式工作台的会话,多轮消息围绕一个会话展开。"""

    __tablename__ = "agent_chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 会话唯一 ID
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)  # 会话所属工作区
    title: Mapped[str] = mapped_column(String(120), default="新会话")  # 会话标题
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 创建会话的用户 ID，可为空


class AgentChatMessage(TimestampMixin, Base):
    """会话消息。assistant 消息保存思维链回看与关联的运行/方案。"""

    __tablename__ = "agent_chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 消息唯一 ID
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_chat_sessions.id"), index=True)  # 所属会话 ID
    role: Mapped[str] = mapped_column(String(16))  # 消息角色：user、assistant 或 system
    content: Mapped[str] = mapped_column(Text)  # 消息正文
    thinking_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # 可回看的模型思考文本，可为空
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)  # 关联 Agent 运行 ID，可为空
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("meal_plans.id"), nullable=True)  # 关联菜单方案 ID，可为空


class ProcurementItem(TimestampMixin, Base):
    """家庭共享采购清单项。"""

    __tablename__ = "procurement_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 采购项唯一 ID
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)  # 所属工作区
    ingredient: Mapped[str] = mapped_column(String(120))  # 采购食材名称
    quantity: Mapped[str] = mapped_column(String(40), default="—")  # 建议采购数量文本
    unit: Mapped[str] = mapped_column(String(20), default="份")  # 采购计量单位
    checked: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否已完成采购
    source: Mapped[str] = mapped_column(String(20), default="manual")  # 采购项来源，例如 manual/chat
    source_id: Mapped[str | None] = mapped_column(String(120), nullable=True)  # 来源记录 ID，可为空
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 勾选完成时间，可为空


class CookingRecord(TimestampMixin, Base):
    """家庭共享下厨足迹文字记录；图片暂由前端本地保留。"""

    __tablename__ = "cooking_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)  # 下厨记录唯一 ID
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)  # 所属工作区
    dish_name: Mapped[str] = mapped_column(String(120), index=True)  # 菜品名称
    cook: Mapped[str] = mapped_column(String(80))  # 下厨人
    cooked_at: Mapped[str] = mapped_column(String(40))  # 下厨日期或时间文本
    servings: Mapped[int] = mapped_column(Integer, default=1)  # 实际用餐人数
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)  # 实际或预计烹饪时长（分钟）
    difficulty: Mapped[str] = mapped_column(String(20), default="家常")  # 难度标签
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)  # 下厨记录标签列表
    ingredients: Mapped[str] = mapped_column(Text, default="")  # 食材记录文本
    story: Mapped[str] = mapped_column(Text, default="")  # 下厨心得或故事
    steps: Mapped[list[dict]] = mapped_column(JSON, default=list)  # 下厨过程步骤列表
    rating: Mapped[float] = mapped_column(Numeric(3, 1), default=5)  # 本次评分
    next_time_note: Mapped[str] = mapped_column(Text, default="")  # 下次改进备注
    image: Mapped[str] = mapped_column(Text, default="")  # 图片地址或前端保存的图片数据
    reviews: Mapped[list[dict]] = mapped_column(JSON, default=list)  # 其他人的评价列表


from app.db import v2_models as v2_models  # noqa: E402,F401

