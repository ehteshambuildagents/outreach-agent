"""Twenty realistic research outputs for reviewing the writer end-to-end.

Each entry has the exact shape ``research_company()`` returns (a ``status`` +
``data`` envelope). They cover the cases that matter: founder present vs. absent,
different tones (technical / playful / casual / mission-driven), metrics-led vs.
customer-led vs. mission-led hooks, and a couple of deliberate skips.

Used by:
  * ``python main.py --samples``  -> generate + manually review 20 live emails
  * ``tests/test_writer.py``      -> drive offline structural tests
"""


def _ok(**data):
    data.setdefault("has_enough_detail", True)
    data.setdefault("team_members", [])
    data.setdefault("additional_hooks", [])
    data.setdefault("notable_customers", [])
    data.setdefault("tech_stack", [])
    for key in ("founder_name", "founder_role", "what_they_do", "target_customer",
                "recent_focus", "unique_hook", "their_mission_or_why", "tone_style",
                "metrics_or_traction", "pricing_model", "company_name"):
        data.setdefault(key, None)
    return {"status": "ok", "research_score": 72, "data": data}


SAMPLES = [
    _ok(
        company_name="Plausible Analytics",
        founder_name="Uku Tomikas", founder_role="Co-founder & CEO",
        what_they_do="Privacy-friendly, open-source web analytics",
        target_customer="privacy-conscious developers and indie founders",
        unique_hook="Crossed $1M ARR fully bootstrapped, no VC money",
        additional_hooks=["Fully open-source", "GDPR-compliant by default"],
        their_mission_or_why="Make web analytics that respects visitor privacy",
        tone_style="plain-spoken, principled, a little contrarian",
        metrics_or_traction="$1M ARR, 12,000+ paying sites",
        notable_customers=["Sentry", "DuckDuckGo"],
        tech_stack=["Elixir", "Phoenix"],
    ),
    _ok(
        company_name="Lyto AI",
        what_they_do="A multi-model AI assistant that picks the best LLM per task",
        target_customer="early-stage product teams",
        unique_hook="Just launched on Product Hunt; the multi-model routing is sharp",
        additional_hooks=["Routes each prompt to the best of several models"],
        tone_style="energetic, product-led",
        recent_focus="Product Hunt launch this week",
    ),
    _ok(
        company_name="Resend",
        founder_name="Zeno Rocha", founder_role="Co-founder & CEO",
        what_they_do="An email API for developers, built on React Email",
        target_customer="developers who hate building transactional email",
        unique_hook="Hit 1 billion emails sent within the first year",
        additional_hooks=["Created the open-source React Email framework"],
        tone_style="developer-first, clean, confident",
        metrics_or_traction="1B+ emails sent, 10k+ developers",
        notable_customers=["Vercel", "Payload"],
    ),
    _ok(
        company_name="Cal.com",
        founder_name="Peer Richelsen", founder_role="Co-founder & CEO",
        what_they_do="Open-source scheduling infrastructure (a Calendly alternative)",
        target_customer="developers and companies that want to self-host scheduling",
        unique_hook="The open-source Calendly alternative — 30k+ GitHub stars",
        additional_hooks=["Self-hostable", "White-label scheduling API"],
        tone_style="open-source, community-driven",
        metrics_or_traction="30,000+ GitHub stars",
    ),
    _ok(
        company_name="Tinybird",
        what_they_do="Turns data into low-latency analytics APIs over ClickHouse",
        target_customer="data engineers building real-time features",
        unique_hook="Lets data teams ship real-time analytics APIs in minutes",
        additional_hooks=["Built on ClickHouse", "Sub-second queries at scale"],
        tone_style="technical, precise",
        tech_stack=["ClickHouse", "Python"],
    ),
    _ok(
        company_name="Maple",
        founder_name="Anna Petrova", founder_role="Founder",
        what_they_do="A budgeting app for couples who share expenses",
        target_customer="couples managing money together",
        unique_hook="Grew to 50,000 couples with zero paid marketing",
        additional_hooks=["Shared-account view", "Split-by-percentage budgets"],
        their_mission_or_why="Take the friction out of money conversations for couples",
        tone_style="warm, friendly, a little playful",
        metrics_or_traction="50,000 couples onboarded",
    ),
    _ok(
        company_name="Forge Robotics",
        founder_name="Daniel Okafor", founder_role="Co-founder & CTO",
        what_they_do="Warehouse picking robots for mid-size fulfilment centres",
        target_customer="mid-size logistics and fulfilment operators",
        unique_hook="Cut picking time 40% in a pilot with a regional 3PL",
        additional_hooks=["Retrofits existing shelving", "No warehouse re-layout needed"],
        tone_style="grounded, engineering-led",
        metrics_or_traction="40% faster picking in pilot",
        notable_customers=["a regional 3PL pilot"],
    ),
    _ok(
        company_name="Quill",
        what_they_do="An AI writing tool that learns your team's tone of voice",
        target_customer="content and marketing teams at startups",
        unique_hook="Trains on your past docs so drafts already sound like your brand",
        tone_style="playful, witty",
        additional_hooks=["Brand-voice training", "One-click tone matching"],
    ),
    _ok(
        company_name="Hearth",
        founder_name="Sofia Reyes", founder_role="Founder & CEO",
        what_they_do="A telehealth platform for menopause care",
        target_customer="women navigating perimenopause and menopause",
        unique_hook="Built specialist menopause care that most clinics overlook",
        their_mission_or_why="Close the gap in women's midlife healthcare",
        tone_style="warm, mission-driven, reassuring",
        notable_customers=["two regional employers"],
    ),
    _ok(
        company_name="Drift Maps",
        founder_name="Kenji Watanabe", founder_role="Co-founder",
        what_they_do="An offline-first maps SDK for outdoor and adventure apps",
        target_customer="developers building hiking, cycling and travel apps",
        unique_hook="Offline-first maps that keep working with no signal on a trail",
        additional_hooks=["Vector tiles cached on-device", "Tiny SDK footprint"],
        tone_style="technical, outdoorsy",
        tech_stack=["Rust", "Swift", "Kotlin"],
    ),
    _ok(
        company_name="Ledgerly",
        what_they_do="Automated bookkeeping for freelancers and solo founders",
        target_customer="freelancers and one-person businesses",
        unique_hook="Categorises a year of transactions in one tax-season afternoon",
        additional_hooks=["Auto-categorisation", "Quarterly tax estimates"],
        tone_style="reassuring, no-jargon",
        pricing_model="flat $12/month",
    ),
    _ok(
        company_name="Beacon Security",
        founder_name="Marcus Hale", founder_role="Co-founder & CEO",
        what_they_do="Continuous penetration testing delivered as a subscription",
        target_customer="Series A-B startups that need SOC 2 fast",
        unique_hook="Helps startups close SOC 2 in weeks instead of months",
        additional_hooks=["Always-on testing", "Findings mapped to SOC 2 controls"],
        tone_style="direct, security-serious",
        metrics_or_traction="120+ startups certified",
    ),
    _ok(
        company_name="Sprout Labs",
        founder_name="Aisha Bello", founder_role="Founder",
        what_they_do="Hands-on science kits delivered monthly to kids",
        target_customer="parents of curious 6-12 year olds",
        unique_hook="Each kit is co-designed with practising classroom teachers",
        their_mission_or_why="Make science feel like play, not homework",
        tone_style="cheerful, playful, parent-friendly",
        metrics_or_traction="35,000 kits shipped",
    ),
    _ok(
        company_name="Cadence",
        what_they_do="A standup and async-update tool that lives in Slack",
        target_customer="remote engineering teams",
        unique_hook="Replaces daily standups with a 2-minute async Slack thread",
        additional_hooks=["No new app to learn", "Auto-summarised blockers"],
        tone_style="casual, remote-work native",
        notable_customers=["GitLab", "Doist"],
    ),
    _ok(
        company_name="Northwind Coffee",
        founder_name="Tom Bridger", founder_role="Founder",
        what_they_do="A direct-trade coffee subscription roasted to order",
        target_customer="home coffee enthusiasts",
        unique_hook="Roasts every bag to order and ships within 24 hours",
        additional_hooks=["Direct-trade sourcing", "Roast-date on every bag"],
        their_mission_or_why="Pay farmers fairly and ship coffee at peak freshness",
        tone_style="craft, down-to-earth",
    ),
    _ok(
        company_name="Vellum",
        founder_name="Priya Nair", founder_role="Co-founder & CEO",
        what_they_do="A workflow tool for testing and versioning LLM prompts",
        target_customer="teams shipping LLM features to production",
        unique_hook="Lets teams version and A/B test prompts like real code",
        additional_hooks=["Prompt regression tests", "Side-by-side model comparisons"],
        tone_style="technical, pragmatic",
        metrics_or_traction="used by 300+ AI teams",
    ),
    _ok(
        company_name="Tidal Fitness",
        what_they_do="A swimming coach app that uses your Apple Watch data",
        target_customer="lap swimmers training for open-water events",
        unique_hook="Turns Apple Watch swim data into a weekly coached plan",
        tone_style="motivating, athletic",
        additional_hooks=["Stroke-efficiency insights", "Open-water race prep plans"],
    ),
    _ok(
        company_name="Garden State Goods",
        founder_name="Maria Castillo", founder_role="Founder",
        what_they_do="A marketplace for New Jersey small-batch food makers",
        target_customer="local shoppers who want to buy from nearby makers",
        unique_hook="Onboarded 200 NJ makers in the first six months",
        their_mission_or_why="Keep more food money in the local community",
        tone_style="local, community-first, warm",
        metrics_or_traction="200 makers, 15,000 orders",
    ),
    _ok(
        company_name="Cobalt Compliance",
        founder_name="Raj Mehta", founder_role="Co-founder & CEO",
        what_they_do="Automated KYC and AML checks for fintech startups",
        target_customer="early fintech teams that need compliance from day one",
        unique_hook="Gets a fintech KYC-ready before its first customer signs up",
        additional_hooks=["One API for KYC + AML", "Audit-ready logs"],
        tone_style="precise, compliance-serious",
        notable_customers=["two neobanks"],
    ),
    _ok(
        company_name="Mosaic",
        founder_name="Elena Furtado", founder_role="Founder & CEO",
        what_they_do="A no-code internal tools builder on top of your database",
        target_customer="ops teams at fast-growing startups",
        unique_hook="Lets ops teams build internal tools without waiting on engineers",
        additional_hooks=["Connects to Postgres in minutes", "Granular role permissions"],
        tone_style="practical, ops-friendly",
        metrics_or_traction="2,000 internal tools built",
        notable_customers=["Ramp", "Brex"],
    ),
]
