#!/usr/bin/env python3
"""生成 PROJECT_GUIDE_FOR_STUDENT.pdf - 使用reportlab支持中文"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, PageBreak, ListFlowable, ListItem)
from reportlab.lib.colors import HexColor, black, white, grey
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PDF_FILE = ROOT / "PROJECT_GUIDE_FOR_STUDENT.pdf"

# 颜色定义
COLOR_TITLE = HexColor('#2980B9')
COLOR_HEADER = HexColor('#34495E')
COLOR_BODY = HexColor('#2C3E50')
COLOR_CODE_BG = HexColor('#F5F5F5')
COLOR_ALT_ROW = HexColor('#FAFAFA')

# 中文字体 - 使用系统自带
import os
if os.path.exists('/System/Library/Fonts/STHeiti Light.ttc'):
    FONT_CJK = '/System/Library/Fonts/STHeiti Light.ttc'
elif os.path.exists('/System/Library/Fonts/PingFang.ttc'):
    FONT_CJK = '/System/Library/Fonts/PingFang.ttc'
elif os.path.exists('/Library/Fonts/Arial Unicode.ttf'):
    FONT_CJK = '/Library/Fonts/Arial Unicode.ttf'
else:
    FONT_CJK = 'Helvetica'  # fallback

def register_cjk_font():
    try:
        pdfmetrics.registerFont(TTFont('CJK', FONT_CJK))
        return 'CJK'
    except:
        return 'Helvetica'

CJK_FONT = register_cjk_font()

def create_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name='MainTitle',
        fontName='Helvetica-Bold' if CJK_FONT == 'Helvetica' else 'CJK',
        fontSize=26,
        textColor=COLOR_TITLE,
        alignment=TA_CENTER,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name='SubTitle',
        fontName='Helvetica' if CJK_FONT == 'Helvetica' else 'CJK',
        fontSize=12,
        textColor=HexColor('#7F8C8D'),
        alignment=TA_CENTER,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name='Section',
        fontName='Helvetica-Bold' if CJK_FONT == 'Helvetica' else 'CJK',
        fontSize=16,
        textColor=COLOR_HEADER,
        spaceBefore=20,
        spaceAfter=10,
        borderPadding=(0, 0, 3, 0),
    ))
    styles.add(ParagraphStyle(
        name='Subsection',
        fontName='Helvetica-Bold' if CJK_FONT == 'Helvetica' else 'CJK',
        fontSize=13,
        textColor=COLOR_HEADER,
        spaceBefore=14,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name='Body',
        fontName='Helvetica' if CJK_FONT == 'Helvetica' else 'CJK',
        fontSize=10,
        textColor=COLOR_BODY,
        alignment=TA_JUSTIFY,
        spaceAfter=8,
        leading=14,
    ))
    styles.add(ParagraphStyle(
        name='CodeBlock',
        fontName='Courier',
        fontSize=8,
        textColor=HexColor('#2C3E50'),
        backColor=COLOR_CODE_BG,
        spaceBefore=4,
        spaceAfter=4,
        leftIndent=10,
        rightIndent=10,
        borderPadding=(5, 5, 5, 5),
    ))
    styles.add(ParagraphStyle(
        name='BulletItem',
        fontName='Helvetica' if CJK_FONT == 'Helvetica' else 'CJK',
        fontSize=10,
        textColor=COLOR_BODY,
        leftIndent=20,
        spaceAfter=4,
        bulletIndent=10,
    ))
    styles.add(ParagraphStyle(
        name='SmallBody',
        fontName='Helvetica' if CJK_FONT == 'Helvetica' else 'CJK',
        fontSize=9,
        textColor=COLOR_BODY,
        spaceAfter=6,
        leading=12,
    ))
    return styles

def make_table_style():
    return TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#34495E')),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#BDC3C7')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, COLOR_ALT_ROW]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ])

def main():
    doc = SimpleDocTemplate(
        str(PDF_FILE),
        pagesize=A4,
        rightMargin=2*cm,
        leftMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm,
    )

    styles = create_styles()
    story = []

    # 标题页
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph("BrokerChain/TDR 实验平台", styles['MainTitle']))
    story.append(Paragraph("完 全 指 南", styles['MainTitle']))
    story.append(Spacer(1, 1.5*cm))
    story.append(Paragraph("项目目录：exp_anvil_broker_qwen", styles['SubTitle']))
    story.append(Paragraph("生成日期：2026-09-19", styles['SubTitle']))
    story.append(Paragraph("适用对象：新生、需要跑实验的研究生", styles['SubTitle']))
    story.append(PageBreak())

    # ===== 第一部分：项目总览 =====
    story.append(Paragraph("第一部分：项目总览", styles['Section']))
    story.append(Paragraph("1.1 这个项目是干什么的", styles['Subsection']))
    story.append(Paragraph(
        "一句话定位：在本地 Anvil 多分片 EVM 上，用真实以太坊主网交易数据，复现并验证 "
        "BrokerChain 论文（broker 机制、relay 兜底、TDR 交易驱动再平衡、B2E 手续费）的实验平台。",
        styles['Body']
    ))
    story.append(Paragraph("研究背景：", styles['SmallBody']))
    bullets1 = [
        "BrokerChain 是一种多分片以太坊的执行架构：链被切成 N 个分片，每个分片是一条独立链",
        "系统里有一批 broker，每个 broker 用同一个地址在每个分片各持有一个子账户",
        "用户跨分片转账时，broker 用自己的两侧子账户垫付资金，分两段交易完成",
        "真实流量有方向性，导致 broker 目的分片账户被持续抽干（drain）",
        "TDR（Transaction-Driven Rebalancing）是本研究提出的对策：按近期需求搬平资金",
    ]
    for b in bullets1:
        story.append(Paragraph(f"• {b}", styles['BulletItem']))

    story.append(Paragraph("1.2 核心术语速查", styles['Subsection']))
    terms_data = [
        ["术语", "含义"],
        ["分片 shard", "一条独立链；本平台里就是一个 anvil 进程"],
        ["CTX", "一笔原始跨分片转账请求（还没上链）"],
        ["Θ1 / Θ2", "服务一笔 CTX 的两段链上交易：源分片段 / 目的分片段"],
        ["broker", "持多分片子账户、用自己的钱垫付的中介"],
        ["relay", "兜底机制：burn-and-mint（销毁 + 铸造）"],
        ["drain", "broker 目的分片子账户被定向流抽干"],
        ["TDR", "Transaction-Driven Rebalancing，交易驱动再平衡"],
        ["B2E", "Broker2Earn，broker 手续费收入机制"],
        ["coordinator", "路由/代铸的中央协调进程"],
        ["守恒门", "自动核对：落定零失败 / 无重无漏 / 账实一致 / 净和 0"],
    ]
    t = Table(terms_data, colWidths=[4.5*cm, 11.5*cm])
    t.setStyle(make_table_style())
    story.append(t)

    # ===== 第二部分：目录结构 =====
    story.append(Paragraph("第二部分：目录结构详解", styles['Section']))
    story.append(Paragraph("2.1 顶层文件夹一览", styles['Subsection']))
    folders_data = [
        ["文件夹", "说明"],
        ["brokerlab/", "核心Python包，共享底座"],
        ["experiments/", "一实验一包，8个实验（exp001-exp008）"],
        ["configs/", "平台级配置档"],
        ["tests/", "纯函数单元测试"],
        ["trace/", "真实数据 ETH_cleaned.csv（328MB）"],
        ["exp_figure_origin/", "手稿原始绘图代码（只读）"],
        ["exp_figure_origin_qwen/", "用重构数据重绘手稿8幅图"],
    ]
    t = Table(folders_data, colWidths=[5*cm, 11*cm])
    t.setStyle(make_table_style())
    story.append(t)

    story.append(Paragraph("2.2 核心包 brokerlab/ 详解", styles['Subsection']))
    modules_data = [
        ["模块", "职责"],
        ["config.py", "YAML配置管理，参数唯一户口"],
        ["chain.py", "anvil子进程集群管理"],
        ["identity.py", "账户派生与nonce管理"],
        ["tx.py", "真实签名转账+度量采集"],
        ["ledger.py", "三本账（confirmed/reserved/incoming）"],
        ["brokerchain.py", "最核心：CTX服务可执行定义"],
        ["real_data.py", "流式读取主网CSV"],
        ["matching.py", "broker匹配策略"],
        ["tdr_policy.py", "TDR再平衡大脑"],
        ["tdr_engine.py", "TDR事件状态机"],
        ["broker_engine.py", "流水线tick引擎"],
        ["plotting.py", "画图样式"],
        ["cli.py", "doctor体检+demo演示"],
    ]
    t = Table(modules_data, colWidths=[4*cm, 12*cm])
    t.setStyle(make_table_style())
    story.append(t)

    story.append(Paragraph("2.3 实验 experiments/ 详解", styles['Subsection']))
    exps_data = [
        ["编号", "主题", "测什么"],
        ["exp001", "relay_vs_broker", "延迟/通信/足迹对比"],
        ["exp002", "broker_drain_no_tdr", "资金耗尽基线"],
        ["exp003", "tdr_on_off", "TDR开/关对照"],
        ["exp004", "b2e_revenue", "B2E手续费"],
        ["exp005", "broker_substrate", "执行基底基准"],
        ["exp006", "rate_invariance", "注入速率不变性"],
        ["exp007", "dynamic_routing", "动态路由"],
        ["exp008", "tdr_schedule_final", "定稿方案评估"],
    ]
    t = Table(exps_data, colWidths=[2.5*cm, 4.5*cm, 9*cm])
    t.setStyle(make_table_style())
    story.append(t)

    story.append(PageBreak())

    # ===== 第三部分：从零开始跑实验 =====
    story.append(Paragraph("第三部分：从零开始跑实验", styles['Section']))

    story.append(Paragraph("3.1 环境准备 - Python虚拟环境", styles['Subsection']))
    story.append(Paragraph("步骤1：创建虚拟环境", styles['SmallBody']))
    story.append(Paragraph("python -m venv .venv", styles['CodeBlock']))
    story.append(Paragraph("source .venv/bin/activate  # Windows用Scripts\\activate", styles['CodeBlock']))
    story.append(Paragraph("步骤2：安装依赖", styles['SmallBody']))
    story.append(Paragraph("pip install -r requirements.txt", styles['CodeBlock']))
    story.append(Paragraph("pip install -e .  # 安装brokerlab包为可编辑模式", styles['CodeBlock']))
    story.append(Paragraph("步骤3：安装anvil（Foundry）", styles['SmallBody']))
    story.append(Paragraph("curl -L https://foundry.paradigm.xyz | bash", styles['CodeBlock']))
    story.append(Paragraph("foundryup", styles['CodeBlock']))
    story.append(Paragraph("anvil --version  # 验证安装", styles['CodeBlock']))

    story.append(Paragraph("3.2 首次体检（必须）", styles['Subsection']))
    story.append(Paragraph("python -m brokerlab doctor --config configs/smoke.yaml", styles['CodeBlock']))

    story.append(Paragraph("3.3 跑最小完整演示（M1）", styles['Subsection']))
    story.append(Paragraph("python -m brokerlab demo --config configs/smoke.yaml", styles['CodeBlock']))

    story.append(Paragraph("3.4 跑单元测试", styles['Subsection']))
    story.append(Paragraph("pytest tests/ -q  # 纯函数测试，无需anvil", styles['CodeBlock']))

    story.append(Paragraph("3.5 跑具体实验", styles['Subsection']))
    story.append(Paragraph("每个实验通用步骤：doctor体检 -> cd进去 -> python run.py -> 退出码0=全部成立",
                           styles['SmallBody']))
    story.append(Paragraph("cd experiments/exp001_relay_vs_broker && python run.py  # 机制对比（2分钟）", styles['CodeBlock']))
    story.append(Paragraph("cd experiments/exp002_broker_drain_no_tdr && python run.py  # 资金耗尽基线", styles['CodeBlock']))
    story.append(Paragraph("cd experiments/exp003_tdr_on_off && python run.py  # TDR对照（正式10分钟）", styles['CodeBlock']))
    story.append(Paragraph("cd experiments/exp008_tdr_schedule_final && python run.py  # 定稿评估（较久）", styles['CodeBlock']))

    story.append(Paragraph("3.6 临时覆盖参数", styles['Subsection']))
    story.append(Paragraph("python run.py --set exp.pairs=20 --set tdr.enabled=true  # 覆盖配置", styles['CodeBlock']))

    story.append(Paragraph("3.7 产物结构", styles['Subsection']))
    story.append(Paragraph("每次运行在 out/<时间戳>/ 下生成：", styles['SmallBody']))
    for b in ["params.json - 配置快照", "ctx_rows.csv - 逐笔源数据",
              "summary.json - 聚合+判据判定", "figs/*.png - 图表", "logs/ - 节点日志"]:
        story.append(Paragraph(f"• {b}", styles['BulletItem']))

    # ===== 第四部分：如何画图 =====
    story.append(Paragraph("第四部分：如何画图", styles['Section']))
    story.append(Paragraph("4.1 两套绘图系统", styles['Subsection']))
    story.append(Paragraph(
        "系统A：实验内嵌绘图 - 每个run.py自己调用brokerlab.plotting画图",
        styles['SmallBody']))
    story.append(Paragraph(
        "系统B：重绘手稿图（exp_figure_origin_qwen/）- 用新数据重绘论文图表",
        styles['SmallBody']))

    story.append(Paragraph("4.2 一键重绘所有手稿图", styles['Subsection']))
    story.append(Paragraph("cd exp_figure_origin_qwen && bash run_all.sh", styles['CodeBlock']))

    story.append(Paragraph("4.3 颜色常量（brokerlab/plotting.py）", styles['Subsection']))
    colors_data = [
        ["常量", "颜色值", "用途"],
        ["COLOR_BROKER", "#56B4E9", "蓝 - broker路径"],
        ["COLOR_RELAY", "#E79016", "橙 - relay路径"],
        ["COLOR_DATA", "#D52700", "红 - 跨片数据量"],
        ["COLOR_ALT", "#009E73", "绿 - 对照组"],
    ]
    t = Table(colors_data, colWidths=[4.5*cm, 3*cm, 8.5*cm])
    t.setStyle(make_table_style())
    story.append(t)

    story.append(PageBreak())

    # ===== 第五部分：配置系统 =====
    story.append(Paragraph("第五部分：配置系统详解", styles['Section']))
    story.append(Paragraph("5.1 配置档说明", styles['Subsection']))
    configs_data = [
        ["配置", "规模", "用途"],
        ["smoke.yaml", "2分片 x 1 broker", "M1演示/回归"],
        ["medium.yaml", "4分片 x 8 broker", "流水线/TDR对比"],
        ["full.yaml", "16分片 x 50 broker", "论文口径（实验室机器）"],
    ]
    t = Table(configs_data, colWidths=[3.5*cm, 5*cm, 7.5*cm])
    t.setStyle(make_table_style())
    story.append(t)

    # ===== 第六部分：方法纪律 =====
    story.append(Paragraph("第六部分：方法纪律（为什么结果可信）", styles['Section']))
    story.append(Paragraph(
        "这是本项目区别于旧三代、也是它能产出可引用证据的根基：",
        styles['SmallBody']))
    disciplines_data = [
        ["红线", "含义"],
        ["逻辑时钟", "负载按块释放，禁墙钟"],
        ["守恒门 G1-G4", "全落定零失败/CTX无重无漏/净和0 wei"],
        ["burn≡mint台账", "BURN增量=relay+TDR销毁额=代铸额"],
        ["setBalance只许setup", "实验过程绝无魔法改余额"],
        ["一切度量实测", "字节/延迟/回执从真实交易采集"],
    ]
    t = Table(disciplines_data, colWidths=[4.5*cm, 11.5*cm])
    t.setStyle(make_table_style())
    story.append(t)

    # ===== 第七部分：8个实验详解 =====
    story.append(Paragraph("第七部分：8个实验详解", styles['Section']))

    story.append(Paragraph("exp001 - relay vs broker 机制对比", styles['Subsection']))
    for b in ["同一批50笔真实CTX，每条路径各执行一次",
              "H1延迟相等：broker 1.09s vs relay 1.12s（差2.5%）",
              "H2跨片通信差约20x：relay 2005B/笔 vs broker 100B"]:
        story.append(Paragraph(f"• {b}", styles['BulletItem']))

    story.append(Paragraph("exp002 - 无TDR的资金耗尽基线", styles['Subsection']))
    for b in ["150笔真实定向流，单进程顺序执行，两档垫资对照",
              "复现手稿C1-C4：dst分片趋零、总资金守恒、relay占比递增"]:
        story.append(Paragraph(f"• {b}", styles['BulletItem']))

    story.append(Paragraph("exp003 - TDR开/关对照", styles['Subsection']))
    for b in ["plain vs tdr 两臂各起新链，同一万笔流",
              "TDR把终审改判relay从27.1%压到0.5%"]:
        story.append(Paragraph(f"• {b}", styles['BulletItem']))

    story.append(Paragraph("exp004 - B2E手续费", styles['Subsection']))
    for b in ["Broker2Earn费用机制第一次上链",
              "三恒等式全True；broker收入=ΣβF"]:
        story.append(Paragraph(f"• {b}", styles['BulletItem']))

    story.append(Paragraph("exp005 - broker执行基底基准", styles['Subsection']))
    for b in ["50个broker引擎装进串行/线程/进程",
              "进程3.80x，线程0.82x（GIL锁成单核）"]:
        story.append(Paragraph(f"• {b}", styles['BulletItem']))

    story.append(Paragraph("exp006 - 注入速率不变性", styles['Subsection']))
    for b in ["扫25-300 CTX/块四个速率档",
              "决策指纹跨12倍速率逐位一致"]:
        story.append(Paragraph(f"• {b}", styles['BulletItem']))

    story.append(Paragraph("exp007 - 动态路由", styles['Subsection']))
    for b in ["coordinator实时指派 vs 静态指派",
              "动态吞吐=静态的0.44-0.62x"]:
        story.append(Paragraph(f"• {b}", styles['BulletItem']))

    story.append(Paragraph("exp008 - 定稿方案正式评估", styles['Subsection']))
    for b in ["五方案同场x多场，聚合论文数据",
              "定稿方案（topup+EWMA ε0.95）relay中位0.05%"]:
        story.append(Paragraph(f"• {b}", styles['BulletItem']))

    story.append(PageBreak())

    # ===== 第八部分：快速参考命令 =====
    story.append(Paragraph("第八部分：快速参考命令", styles['Section']))
    commands = [
        ("cd /Users/tangyucinder/开发/exp_anvil_broker_qwen", ""),
        ("source .venv/bin/activate", "激活虚拟环境"),
        ("python -m brokerlab doctor --config configs/smoke.yaml", "体检"),
        ("python -m brokerlab demo --config configs/smoke.yaml", "演示"),
        ("pytest tests/ -q", "测试"),
        ("cd experiments/exp001_relay_vs_broker && python run.py", "跑实验"),
        ("cd exp_figure_origin_qwen && bash run_all.sh", "重绘所有手稿图"),
    ]
    for cmd, comment in commands:
        story.append(Paragraph(cmd, styles['CodeBlock']))
        if comment:
            story.append(Paragraph(f"  # {comment}", styles['SmallBody']))

    # ===== 第九部分：建议阅读顺序 =====
    story.append(Paragraph("第九部分：建议阅读顺序", styles['Section']))
    reading_order = [
        "本文档（项目全貌）",
        "README.md（入口+结果摘要）",
        "EXPERIMENT_DESIGN_CN.md（为什么这样设计）",
        "EXPERIMENT_REPORT_CN.md（8个实验结论）",
        "CODE_READING_GUIDE_CN.md（逐文件读代码）",
        "亲手跑一遍：doctor -> demo -> pytest -> exp001",
        "AUDIT_fatal_flaws_CN.md（理解F1-F9）",
        "PLAN_refactor_CN.md（里程碑决策）",
    ]
    for i, item in enumerate(reading_order, 1):
        story.append(Paragraph(f"{i}. {item}", styles['BulletItem']))

    # 生成
    doc.build(story)
    print(f"PDF已生成：{PDF_FILE}")


if __name__ == "__main__":
    main()
