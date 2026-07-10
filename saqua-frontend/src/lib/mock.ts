export const campaigns = [
  { name: "SaaS Founders - US", sub: "Targeting Series A SaaS founders in the US", status: "Active", sent: 320, replies: 48, rate: "15.0%" },
  { name: "AI Tools - Global", sub: "Founders building AI products", status: "Active", sent: 280, replies: 35, rate: "12.5%" },
  { name: "Seed Funded - US & CA", sub: "Recently funded seed stage companies", status: "Paused", sent: 210, replies: 28, rate: "13.3%" },
  { name: "Productivity Apps", sub: "Founders building productivity tools", status: "Completed", sent: 430, replies: 58, rate: "13.5%" },
  { name: "Developer Tools", sub: "Outreach to devtool founders", status: "Active", sent: 210, replies: 26, rate: "12.4%" },
];

export const prospects = [
  { name: "Michael Chen", company: "Linear", role: "Founder & CEO", status: "Replied", score: 92, last: "2h ago", email: "michael@linear.app", location: "San Francisco, CA" },
  { name: "Sarah Thompson", company: "Notion", role: "Co-founder", status: "Contacted", score: 88, last: "1h ago", email: "sarah@notion.so", location: "San Francisco, CA" },
  { name: "David Park", company: "Vercel", role: "CEO", status: "Qualified", score: 85, last: "3h ago", email: "david@vercel.com", location: "New York, NY" },
  { name: "Alex Garcia", company: "Retool", role: "Founder", status: "Contacted", score: 80, last: "5h ago", email: "alex@retool.com", location: "San Francisco, CA" },
  { name: "Jordan Lee", company: "Nodel", role: "Co-founder", status: "Qualified", score: 78, last: "1d ago", email: "jordan@nodel.ai", location: "Austin, TX" },
  { name: "Taylor Wilson", company: "Supabase", role: "Founder", status: "Not Contacted", score: 72, last: "-", email: "taylor@supabase.com", location: "Remote" },
  { name: "Chris Blake", company: "LlamaIndex", role: "Founder & CEO", status: "Not Contacted", score: 70, last: "-", email: "chris@llamaindex.ai", location: "Seattle, WA" },
];

export const activity = [
  { title: "Replied", body: "Sounds interesting! Can you send more info?", time: "2m ago", tone: "success" as const },
  { title: "Email opened", body: "Opened email about Linear's growth", time: "15m ago", tone: "accent" as const },
  { title: "Email sent", body: "Quick question about internal tools", time: "1h ago", tone: "neutral" as const },
  { title: "Added to campaign", body: "SaaS Founders - US", time: "2h ago", tone: "success" as const },
];

export const integrations = [
  ["Gmail", "Send & receive emails", "Connected"],
  ["Outlook", "Send & receive emails", "Connect"],
  ["LinkedIn", "Find prospects & connect", "Connected"],
  ["HubSpot", "Sync contacts & deals", "Connect"],
  ["Salesforce", "Sync contacts & deals", "Connect"],
  ["Airtable", "Sync data & enrich", "Connect"],
  ["Clay", "Enrich prospect data", "Connect"],
  ["Zapier", "Automate workflows", "Connect"],
] as const;

export const agents = [
  ["Prospect Discovery", "Finding qualified accounts", "running", "1.8s", "$0.006", "91%"],
  ["Research", "Crawling site and evidence graph", "running", "14.2s", "$0.041", "88%"],
  ["Lead Qualification", "Scoring fit and timing", "completed", "0.7s", "$0.002", "82%"],
  ["Strategy", "Choosing outreach angle", "completed", "1.1s", "$0.004", "79%"],
  ["Writer", "Drafting first email", "running", "6.4s", "$0.018", "86%"],
  ["Guard", "Checking safety and deliverability", "completed", "0.3s", "$0.000", "99%"],
  ["Automation", "Scheduling next step", "queued", "-", "$0.000", "93%"],
];
