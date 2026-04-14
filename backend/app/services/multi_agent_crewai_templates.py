"""
CrewAI 多智能体模板定义：
- 场景元信息
- 角色模板
- 任务模板
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal

MultiAgentScene = Literal["finance_research", "market_ops", "compliance_risk", "product_strategy"]
ParadigmTag = Literal["react", "plan_execute", "rewoo", "reflection", "reporting"]


@dataclass(frozen=True)
class AgentTemplate:
    agent_id: str
    role: str
    goal: str
    backstory: str
    allow_delegation: bool = False
    paradigm: ParadigmTag = "plan_execute"


@dataclass(frozen=True)
class TaskTemplate:
    task_id: str
    agent_id: str
    description_template: str
    expected_output: str


@dataclass(frozen=True)
class SceneTemplate:
    scene: MultiAgentScene
    display_name: str
    description: str
    workflow: str
    paradigm_mix: str
    agents: List[AgentTemplate]
    tasks: List[TaskTemplate]


FINANCE_SCENE = SceneTemplate(
    scene="finance_research",
    display_name="金融 / 投研",
    description="最成熟、最具商业价值：研究-分析-风控-评审-报告闭环。",
    workflow="Research -> 基本面 -> 技术面 -> 风险 -> 评审 -> 报告",
    paradigm_mix="ReAct + Plan&Execute + ReWOO + Reflection",
    agents=[
        AgentTemplate(
            agent_id="researcher",
            role="研究员 Agent（ReAct）",
            goal="快速抓取并汇总财报、新闻、研报、监管文件等一手材料",
            backstory="擅长观察-行动-再观察迭代，快速补齐信息缺口。",
            allow_delegation=True,
            paradigm="react",
        ),
        AgentTemplate(
            agent_id="fundamental",
            role="基本面分析师 Agent（Plan & Execute）",
            goal="拆解财务三表并计算关键指标，输出结构化基本面观点",
            backstory="擅长计划执行，强调指标口径一致和可追溯。",
            paradigm="plan_execute",
        ),
        AgentTemplate(
            agent_id="technical",
            role="技术分析师 Agent（ReAct）",
            goal="结合量价关系和技术形态给出趋势判断",
            backstory="擅长短中周期趋势观察与关键位识别。",
            paradigm="react",
        ),
        AgentTemplate(
            agent_id="risk",
            role="风险评估 Agent（ReWOO）",
            goal="批量核查政策、舆情、流动性、合规风险并形成风险矩阵",
            backstory="擅长先规划风险项依赖，再分步执行核查。",
            paradigm="rewoo",
        ),
        AgentTemplate(
            agent_id="director",
            role="投研总监 Agent（Reflection）",
            goal="反思并评审前序结论，纠偏后给出最终评级与建议",
            backstory="偏审慎，关注证据一致性和反例验证。",
            paradigm="reflection",
        ),
        AgentTemplate(
            agent_id="reporter",
            role="报告生成 Agent",
            goal="将最终结论转为标准化投研报告文本",
            backstory="擅长结构化输出，便于导出 PDF/Word。",
            paradigm="reporting",
        ),
    ],
    tasks=[
        TaskTemplate(
            "t1_research",
            "researcher",
            "用户问题：{query}\n标的：{symbol}\n时间窗口：{time_window}\n风险偏好：{risk_preference}\n执行 Research 阶段，输出证据清单与摘要。",
            "研究证据清单",
        ),
        TaskTemplate("t2_fundamental", "fundamental", "基于研究证据，完成基本面分析（ROE/PE/Growth 等）并给出结论。", "基本面分析报告"),
        TaskTemplate("t3_technical", "technical", "结合可得市场信息，给出技术面趋势与关键位判断。", "技术面分析结果"),
        TaskTemplate("t4_risk", "risk", "执行风险核查：政策、舆情、流动性、合规，输出风险矩阵。", "风险矩阵与等级"),
        TaskTemplate("t5_review", "director", "反思并评审所有前序输出，纠错并给出最终评级与建议。", "最终评级与目标建议"),
        TaskTemplate("t6_report", "reporter", "汇总为标准投研报告（含结论、依据、风险、行动建议）。", "最终投研报告"),
    ],
)


GENERIC_SCENE_TEMPLATES: Dict[MultiAgentScene, SceneTemplate] = {
    "market_ops": SceneTemplate(
        scene="market_ops",
        display_name="市场运营 / 增长",
        description="洞察-策略-投放-复盘，适合活动与增长决策。",
        workflow="规划 -> 探索 -> 批处理 -> 评审",
        paradigm_mix="ReAct + Plan&Execute + ReWOO + Reflection",
        agents=[
            AgentTemplate("planner", "任务规划 Agent（Plan & Execute）", "拆解任务并定义执行顺序", "把复杂问题拆成可执行步骤。", paradigm="plan_execute"),
            AgentTemplate("explorer", "信息探索 Agent（ReAct）", "边观察边补证据，快速形成事实底座", "擅长快速迭代验证。", paradigm="react"),
            AgentTemplate("batcher", "批处理执行 Agent（ReWOO）", "按依赖关系批量执行子任务并回传中间结果", "先规划依赖再执行。", paradigm="rewoo"),
            AgentTemplate("reviewer", "评审 Agent（Reflection）", "复盘纠偏，输出稳健结论", "偏审慎，擅长发现漏洞。", paradigm="reflection"),
        ],
        tasks=[
            TaskTemplate("t1_plan", "planner", "用户问题：{query}\n先给出执行计划。", "执行计划"),
            TaskTemplate("t2_explore", "explorer", "按计划完成信息探索并形成事实清单。", "事实清单"),
            TaskTemplate("t3_batch", "batcher", "对关键子任务做依赖化批处理执行。", "批处理结果"),
            TaskTemplate("t4_review", "reviewer", "评审并输出最终答案（含风险与建议）。", "最终答案"),
        ],
    ),
    "compliance_risk": SceneTemplate(
        scene="compliance_risk",
        display_name="法务合规 / 风险控制",
        description="条款审查、政策核验、风险分级与整改建议。",
        workflow="规划 -> 探索 -> 批处理 -> 评审",
        paradigm_mix="ReAct + Plan&Execute + ReWOO + Reflection",
        agents=[
            AgentTemplate("planner", "任务规划 Agent（Plan & Execute）", "拆解任务并定义执行顺序", "把复杂问题拆成可执行步骤。", paradigm="plan_execute"),
            AgentTemplate("explorer", "信息探索 Agent（ReAct）", "边观察边补证据，快速形成事实底座", "擅长快速迭代验证。", paradigm="react"),
            AgentTemplate("batcher", "批处理执行 Agent（ReWOO）", "按依赖关系批量执行子任务并回传中间结果", "先规划依赖再执行。", paradigm="rewoo"),
            AgentTemplate("reviewer", "评审 Agent（Reflection）", "复盘纠偏，输出稳健结论", "偏审慎，擅长发现漏洞。", paradigm="reflection"),
        ],
        tasks=[
            TaskTemplate("t1_plan", "planner", "用户问题：{query}\n先给出执行计划。", "执行计划"),
            TaskTemplate("t2_explore", "explorer", "按计划完成信息探索并形成事实清单。", "事实清单"),
            TaskTemplate("t3_batch", "batcher", "对关键子任务做依赖化批处理执行。", "批处理结果"),
            TaskTemplate("t4_review", "reviewer", "评审并输出最终答案（含风险与建议）。", "最终答案"),
        ],
    ),
    "product_strategy": SceneTemplate(
        scene="product_strategy",
        display_name="产品策略 / 规划",
        description="需求研究、竞品分析、路线图与里程碑产出。",
        workflow="规划 -> 探索 -> 批处理 -> 评审",
        paradigm_mix="ReAct + Plan&Execute + ReWOO + Reflection",
        agents=[
            AgentTemplate("planner", "任务规划 Agent（Plan & Execute）", "拆解任务并定义执行顺序", "把复杂问题拆成可执行步骤。", paradigm="plan_execute"),
            AgentTemplate("explorer", "信息探索 Agent（ReAct）", "边观察边补证据，快速形成事实底座", "擅长快速迭代验证。", paradigm="react"),
            AgentTemplate("batcher", "批处理执行 Agent（ReWOO）", "按依赖关系批量执行子任务并回传中间结果", "先规划依赖再执行。", paradigm="rewoo"),
            AgentTemplate("reviewer", "评审 Agent（Reflection）", "复盘纠偏，输出稳健结论", "偏审慎，擅长发现漏洞。", paradigm="reflection"),
        ],
        tasks=[
            TaskTemplate("t1_plan", "planner", "用户问题：{query}\n先给出执行计划。", "执行计划"),
            TaskTemplate("t2_explore", "explorer", "按计划完成信息探索并形成事实清单。", "事实清单"),
            TaskTemplate("t3_batch", "batcher", "对关键子任务做依赖化批处理执行。", "批处理结果"),
            TaskTemplate("t4_review", "reviewer", "评审并输出最终答案（含风险与建议）。", "最终答案"),
        ],
    ),
}


def get_scene_template(scene: MultiAgentScene) -> SceneTemplate:
    if scene == "finance_research":
        return FINANCE_SCENE
    return GENERIC_SCENE_TEMPLATES[scene]

