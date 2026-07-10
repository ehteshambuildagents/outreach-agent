# MVP Validation - Gated Pipeline

Companies evaluated: 32
Final classifications: {'sendable': 10, 'research_failed': 6, 'guard_blocked': 15, 'qualification_blocked': 1}
Rating breakdown: {'Good': 10, 'Unusable': 7, 'Average': 15}
Excellent+Good: 10 (31.2% of all companies)
Excellent+Good among qualification-passed: 40.0%
Average estimated cost / prospect: $0.068
Average latency / prospect: 72.7s

| # | Company | Domain | Research | Qual | Strategy | Guard | Final | Rating | Cost | Latency |
|---:|---|---|---|---|---|---|---|---|---:|---:|
| 1 | Nick Automations | nickautomations.com | ok | continue (48) | draft | ALLOW (0) | sendable | Good | $0.063492 | 70.44s |
| 2 | FlowMate | flowmate.io | error |  () |  |  () | research_failed | Unusable | $0 | 127.27s |
| 3 | Netlify | netlify.app | ok | high_priority (78) | draft | ALLOW (0) | sendable | Good | $0.101988 | 98.12s |
| 4 | Bacancytechnology | bacancytechnology.com | error |  () |  |  () | research_failed | Unusable | $0 | 6.58s |
| 5 | Scalevista | scalevista.com | ok | continue (54) | sequence | BLOCK (85) | guard_blocked | Average | $0.119928 | 112.65s |
| 6 | Zylo | zylo.com | ok | continue (66) | sequence | ALLOW (0) | sendable | Good | $0.122421 | 102.3s |
| 7 | Zencoder | zencoder.ai | ok | continue (67) | sequence | ALLOW (0) | sendable | Good | $0.101103 | 91.57s |
| 8 | viaSocket | viasocket.com | ok | continue (65) | sequence | ALLOW (0) | sendable | Good | $0.104943 | 107.94s |
| 9 | Userpilot | userpilot.com | ok | high_priority (79) | sequence | ALLOW (0) | sendable | Good | $0.098835 | 92.81s |
| 10 | Webase Global | webase.global | ok | continue (66) | sequence | ALLOW (0) | sendable | Good | $0.052221 | 65.45s |
| 11 | Text | text.com | ok | high_priority (82) | sequence | ALLOW (6) | sendable | Good | $0.098673 | 87.22s |
| 12 | SF AI Labs | sfailabs.com | ok | continue (50) | draft | BLOCK (85) | guard_blocked | Average | $0.049989 | 47.52s |
| 13 | Gain Solutions | gainhq.com | ok | continue (69) | draft | BLOCK (91) | guard_blocked | Average | $0.093618 | 82.42s |
| 14 | FixnHour | fixnhour.com | ok | high_priority (79) | draft | BLOCK (85) | guard_blocked | Average | $0.055353 | 105.85s |
| 15 | CompanionLink | companionlink.com | ok | continue (50) | draft | BLOCK (85) | guard_blocked | Average | $0.079494 | 117.59s |
| 16 | CIGen | cigen.io | ok | continue (53) | draft | BLOCK (85) | guard_blocked | Average | $0.119139 | 100.93s |
| 17 | Atiba | atiba.com | ok | continue (50) | draft | BLOCK (85) | guard_blocked | Average | $0.043899 | 57.6s |
| 18 | Velcod | velcod.com | ok | high_priority (70) | sequence | BLOCK (85) | guard_blocked | Average | $0.104784 | 96.27s |
| 19 | Pazi | pazi.ai | skip |  () |  |  () | research_failed | Unusable | $0.005637 | 14.45s |
| 20 | Klavis AI | klavis.ai | ok | continue (63) | draft | BLOCK (85) | guard_blocked | Average | $0.046482 | 56.34s |
| 21 | Avenai | avenai.io | ok | continue (51) | draft | BLOCK (97) | guard_blocked | Average | $0.054744 | 44.74s |
| 22 | Wiroxa | wiroxa.dev | ok | reject (62) |  |  () | qualification_blocked | Unusable | $0.030375 | 41.33s |
| 23 | TRAILBLU | trailblu.com | skip |  () |  |  () | research_failed | Unusable | $0.00567 | 11.94s |
| 24 | TheTestMart | thetestmart.com | ok | continue (55) | sequence | BLOCK (91) | guard_blocked | Average | $0.123102 | 108.94s |
| 25 | New Relic | newrelic.com | ok | continue (53) | sequence | ALLOW (0) | sendable | Good | $0.112773 | 89.0s |
| 26 | Developer Labs AI | developerlabs.ai | skip |  () |  |  () | research_failed | Unusable | $0.00564 | 11.29s |
| 27 | RocketHub | rockethub.com | ok | continue (68) | draft | ALLOW (0) | sendable | Good | $0.064947 | 74.94s |
| 28 | Netguru | netguru.com | ok | continue (67) | draft | BLOCK (85) | guard_blocked | Average | $0.045342 | 43.94s |
| 29 | Ema | ema.ai | ok | high_priority (71) | draft | BLOCK (85) | guard_blocked | Average | $0.041799 | 42.32s |
| 30 | Digital Samba | digitalsamba.com | ok | continue (57) | draft | BLOCK (85) | guard_blocked | Average | $0.055536 | 69.99s |
| 31 | Decktopus | decktopus.com | ok | high_priority (83) | draft | BLOCK (85) | guard_blocked | Average | $0.161613 | 127.11s |
| 32 | Codebenders | codebenders.ai | skip |  () |  |  () | research_failed | Unusable | $0.012267 | 19.32s |


## Old vs New Comparison

| Metric | Previous baseline | New gated run |
|---|---:|---:|
| Companies | 32 | 32 |
| Excellent + Good | 15 (46.9%) | 10 (31.2%) |
| Research failures | 5 (15.6%) | 6 (18.8%) |
| Writer failures | 1 | 0 |
| Guard blocks | 0 | 15 |
| Qualification holds/blocks | 22 | 1 |
| Average cost / prospect | $0.0729 | $0.0680 |
| Average latency / prospect | 75.3s | 72.7s |

## Remaining Failures

Research failures: FlowMate, Bacancytechnology, Pazi, TRAILBLU, Developer Labs AI, Codebenders

Guard blocks were primarily missing-recipient blocks after the writer produced a draft but no verified person/recipient was available.
