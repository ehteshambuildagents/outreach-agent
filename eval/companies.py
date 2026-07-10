"""Curated stress-test set: 100+ real companies across 15 industries.

Deliberately mixes big enterprises (often behind bot-protection), fast static
sites, JS-heavy SPAs, and thin local/restaurant sites so the evaluation exercises
the FULL range of real-world conditions — not a happy path.

Each entry: (name, url, industry).
"""

COMPANIES = [
    # ── SaaS ──────────────────────────────────────────────────────────
    ("Slack", "https://slack.com", "SaaS"),
    ("Notion", "https://www.notion.so", "SaaS"),
    ("Airtable", "https://www.airtable.com", "SaaS"),
    ("Asana", "https://asana.com", "SaaS"),
    ("Monday.com", "https://monday.com", "SaaS"),
    ("HubSpot", "https://www.hubspot.com", "SaaS"),
    ("Calendly", "https://calendly.com", "SaaS"),
    ("Loom", "https://www.loom.com", "SaaS"),

    # ── AI startups ───────────────────────────────────────────────────
    ("OpenAI", "https://openai.com", "AI"),
    ("Anthropic", "https://www.anthropic.com", "AI"),
    ("Hugging Face", "https://huggingface.co", "AI"),
    ("Cohere", "https://cohere.com", "AI"),
    ("Perplexity", "https://www.perplexity.ai", "AI"),
    ("Mistral AI", "https://mistral.ai", "AI"),
    ("Runway", "https://runwayml.com", "AI"),
    ("ElevenLabs", "https://elevenlabs.io", "AI"),

    # ── E-commerce ────────────────────────────────────────────────────
    ("Shopify", "https://www.shopify.com", "E-commerce"),
    ("Etsy", "https://www.etsy.com", "E-commerce"),
    ("Warby Parker", "https://www.warbyparker.com", "E-commerce"),
    ("Allbirds", "https://www.allbirds.com", "E-commerce"),
    ("Glossier", "https://www.glossier.com", "E-commerce"),
    ("Casper", "https://casper.com", "E-commerce"),
    ("Chewy", "https://www.chewy.com", "E-commerce"),
    ("Gymshark", "https://www.gymshark.com", "E-commerce"),

    # ── Marketing agencies ────────────────────────────────────────────
    ("VaynerMedia", "https://vaynermedia.com", "Marketing agency"),
    ("Ogilvy", "https://www.ogilvy.com", "Marketing agency"),
    ("Huge", "https://www.hugeinc.com", "Marketing agency"),
    ("Wpromote", "https://www.wpromote.com", "Marketing agency"),
    ("Single Grain", "https://www.singlegrain.com", "Marketing agency"),
    ("WebFX", "https://www.webfx.com", "Marketing agency"),
    ("Disruptive Advertising", "https://disruptiveadvertising.com", "Marketing agency"),

    # ── Healthcare ────────────────────────────────────────────────────
    ("Zocdoc", "https://www.zocdoc.com", "Healthcare"),
    ("Teladoc Health", "https://www.teladochealth.com", "Healthcare"),
    ("Ro", "https://ro.co", "Healthcare"),
    ("Hims", "https://www.forhims.com", "Healthcare"),
    ("Oura", "https://ouraring.com", "Healthcare"),
    ("Carbon Health", "https://carbonhealth.com", "Healthcare"),
    ("Cerebral", "https://www.cerebral.com", "Healthcare"),
    ("One Medical", "https://www.onemedical.com", "Healthcare"),

    # ── Finance / fintech ─────────────────────────────────────────────
    ("Stripe", "https://stripe.com", "Finance"),
    ("Square", "https://squareup.com", "Finance"),
    ("Plaid", "https://plaid.com", "Finance"),
    ("Robinhood", "https://robinhood.com", "Finance"),
    ("Chime", "https://www.chime.com", "Finance"),
    ("Brex", "https://www.brex.com", "Finance"),
    ("Ramp", "https://ramp.com", "Finance"),
    ("Wise", "https://wise.com", "Finance"),

    # ── Manufacturing ─────────────────────────────────────────────────
    ("Caterpillar", "https://www.caterpillar.com", "Manufacturing"),
    ("3M", "https://www.3m.com", "Manufacturing"),
    ("Siemens", "https://www.siemens.com", "Manufacturing"),
    ("Honeywell", "https://www.honeywell.com", "Manufacturing"),
    ("Bosch", "https://www.bosch.com", "Manufacturing"),
    ("Rockwell Automation", "https://www.rockwellautomation.com", "Manufacturing"),
    ("John Deere", "https://www.deere.com", "Manufacturing"),

    # ── Law firms ─────────────────────────────────────────────────────
    ("Baker McKenzie", "https://www.bakermckenzie.com", "Law firm"),
    ("DLA Piper", "https://www.dlapiper.com", "Law firm"),
    ("Skadden", "https://www.skadden.com", "Law firm"),
    ("Latham & Watkins", "https://www.lw.com", "Law firm"),
    ("Clifford Chance", "https://www.cliffordchance.com", "Law firm"),
    ("White & Case", "https://www.whitecase.com", "Law firm"),
    ("Sidley Austin", "https://www.sidley.com", "Law firm"),
    ("Kirkland & Ellis", "https://www.kirkland.com", "Law firm"),

    # ── Education / edtech ────────────────────────────────────────────
    ("Coursera", "https://www.coursera.org", "Education"),
    ("Udemy", "https://www.udemy.com", "Education"),
    ("Duolingo", "https://www.duolingo.com", "Education"),
    ("Khan Academy", "https://www.khanacademy.org", "Education"),
    ("edX", "https://www.edx.org", "Education"),
    ("Chegg", "https://www.chegg.com", "Education"),
    ("Quizlet", "https://quizlet.com", "Education"),
    ("Codecademy", "https://www.codecademy.com", "Education"),

    # ── Restaurants ───────────────────────────────────────────────────
    ("Chipotle", "https://www.chipotle.com", "Restaurant"),
    ("Sweetgreen", "https://www.sweetgreen.com", "Restaurant"),
    ("Shake Shack", "https://www.shakeshack.com", "Restaurant"),
    ("Domino's", "https://www.dominos.com", "Restaurant"),
    ("Starbucks", "https://www.starbucks.com", "Restaurant"),
    ("Panera Bread", "https://www.panerabread.com", "Restaurant"),
    ("CAVA", "https://cava.com", "Restaurant"),

    # ── Local businesses ──────────────────────────────────────────────
    ("Blue Bottle Coffee", "https://bluebottlecoffee.com", "Local business"),
    ("Philz Coffee", "https://www.philzcoffee.com", "Local business"),
    ("Intelligentsia Coffee", "https://www.intelligentsiacoffee.com", "Local business"),
    ("Tartine Bakery", "https://tartinebakery.com", "Local business"),
    ("Orangetheory Fitness", "https://www.orangetheory.com", "Local business"),
    ("Planet Fitness", "https://www.planetfitness.com", "Local business"),
    ("Crunch Fitness", "https://www.crunch.com", "Local business"),

    # ── Open-source companies ─────────────────────────────────────────
    ("GitLab", "https://about.gitlab.com", "Open source"),
    ("Red Hat", "https://www.redhat.com", "Open source"),
    ("Canonical", "https://canonical.com", "Open source"),
    ("HashiCorp", "https://www.hashicorp.com", "Open source"),
    ("Grafana Labs", "https://grafana.com", "Open source"),
    ("Elastic", "https://www.elastic.co", "Open source"),
    ("MongoDB", "https://www.mongodb.com", "Open source"),
    ("Docker", "https://www.docker.com", "Open source"),

    # ── Developer tools ───────────────────────────────────────────────
    ("GitHub", "https://github.com", "Developer tools"),
    ("Vercel", "https://vercel.com", "Developer tools"),
    ("Netlify", "https://www.netlify.com", "Developer tools"),
    ("Postman", "https://www.postman.com", "Developer tools"),
    ("JetBrains", "https://www.jetbrains.com", "Developer tools"),
    ("DigitalOcean", "https://www.digitalocean.com", "Developer tools"),
    ("Sentry", "https://sentry.io", "Developer tools"),
    ("Linear", "https://linear.app", "Developer tools"),
    ("Supabase", "https://supabase.com", "Developer tools"),

    # ── B2B ───────────────────────────────────────────────────────────
    ("Salesforce", "https://www.salesforce.com", "B2B"),
    ("Workday", "https://www.workday.com", "B2B"),
    ("SAP", "https://www.sap.com", "B2B"),
    ("ServiceNow", "https://www.servicenow.com", "B2B"),
    ("Atlassian", "https://www.atlassian.com", "B2B"),
    ("DocuSign", "https://www.docusign.com", "B2B"),
    ("Zendesk", "https://www.zendesk.com", "B2B"),
    ("Gong", "https://www.gong.io", "B2B"),

    # ── B2C ───────────────────────────────────────────────────────────
    ("Spotify", "https://www.spotify.com", "B2C"),
    ("Netflix", "https://www.netflix.com", "B2C"),
    ("Airbnb", "https://www.airbnb.com", "B2C"),
    ("Uber", "https://www.uber.com", "B2C"),
    ("DoorDash", "https://www.doordash.com", "B2C"),
    ("Peloton", "https://www.onepeloton.com", "B2C"),
    ("Calm", "https://www.calm.com", "B2C"),
    ("Headspace", "https://www.headspace.com", "B2C"),
]
