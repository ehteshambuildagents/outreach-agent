# MVP Validation - Recipient Discovery Fix

Companies evaluated: 32
Final classifications: {'qualification_blocked': 3, 'sendable': 25, 'research_failed': 4}
Manual rating breakdown: {'Unusable': 7, 'Good': 14, 'Average': 11}
Excellent+Good: 14 (43.8% of all companies)
Excellent+Good among qualification-passed: 56.0%
Guard blocks: 0
Missing-recipient guard blocks: 0
Average estimated cost / prospect: $0.1015
Average latency / prospect: 96.4s

## Gated Baseline Comparison

Baseline guard blocks: 15 -> 0
Baseline missing-recipient guard blocks: 15 -> 0
Baseline writer failures: 0 -> 0
Baseline Excellent+Good: 10 -> 14
Baseline avg cost: $0.068 -> $0.1015
Baseline avg latency: 72.7s -> 96.4s

| # | Company | Domain | Research | Qual | Strategy | Guard | Final | Manual Rating | Cost | Latency |
|---:|---|---|---|---|---|---|---|---|---:|---:|
| 1 | Nick Automations | nickautomations.com | ok | research_more (46) |  |  () | qualification_blocked | Unusable | $0.053313 | 81.3s |
| 2 | FlowMate | flowmate.io | ok | continue (65) | sequence | ALLOW (0) | sendable | Good | $0.127188 | 181.2s |
| 3 | Netlify | netlify.app | ok | continue (67) | sequence | ALLOW (0) | sendable | Good | $0.128166 | 123.14s |
| 4 | Bacancytechnology | bacancytechnology.com | error |  () |  |  () | research_failed | Unusable | $0 | 4.88s |
| 5 | Scalevista | scalevista.com | ok | high_priority (82) | sequence | ALLOW (0) | sendable | Average | $0.339906 | 253.53s |
| 6 | Zylo | zylo.com | ok | continue (66) | sequence | ALLOW (0) | sendable | Good | $0.116334 | 94.45s |
| 7 | Zencoder | zencoder.ai | ok | high_priority (83) | sequence | ALLOW (0) | sendable | Good | $0.100917 | 86.01s |
| 8 | viaSocket | viasocket.com | ok | high_priority (80) | sequence | ALLOW (0) | sendable | Good | $0.111915 | 108.46s |
| 9 | Userpilot | userpilot.com | ok | high_priority (95) | sequence | ALLOW (0) | sendable | Good | $0.114504 | 103.3s |
| 10 | Webase Global | webase.global | ok | high_priority (70) | sequence | ALLOW (0) | sendable | Average | $0.053787 | 51.98s |
| 11 | Text | text.com | ok | continue (66) | sequence | ALLOW (0) | sendable | Good | $0.110481 | 97.13s |
| 12 | SF AI Labs | sfailabs.com | ok | continue (64) | sequence | ALLOW (0) | sendable | Good | $0.104613 | 88.25s |
| 13 | Gain Solutions | gainhq.com | ok | continue (53) | draft | ALLOW (6) | sendable | Average | $0.100728 | 88.81s |
| 14 | fixnhour | fixnhour.com | ok | high_priority (79) | sequence | ALLOW (0) | sendable | Average | $0.093528 | 93.44s |
| 15 | CompanionLink | companionlink.com | ok | high_priority (80) | sequence | ALLOW (8) | sendable | Average | $0.108327 | 87.28s |
| 16 | CIGen | cigen.io | ok | continue (66) | sequence | ALLOW (0) | sendable | Average | $0.138174 | 126.7s |
| 17 | Atiba | atiba.com | ok | continue (66) | sequence | ALLOW (0) | sendable | Good | $0.053673 | 67.42s |
| 18 | Velcod | velcod.com | ok | high_priority (80) | sequence | ALLOW (0) | sendable | Average | $0.167022 | 139.79s |
| 19 | Pazi | pazi.ai | skip |  () |  |  () | research_failed | Unusable | $0.014535 | 36.48s |
| 20 | Klavis AI | klavis.ai | ok | high_priority (92) | draft | ALLOW (0) | sendable | Good | $0.104655 | 96.38s |
| 21 | Avenai | avenai.io | ok | continue (60) | draft | ALLOW (0) | sendable | Good | $0.056925 | 62.23s |
| 22 | Wiroxa | wiroxa.dev | ok | reject (81) |  |  () | qualification_blocked | Unusable | $0.038334 | 48.61s |
| 23 | TRAILBLU | trailblu.com | skip |  () |  |  () | research_failed | Unusable | $0.019926 | 26.89s |
| 24 | TheTestMart | thetestmart.com | ok | high_priority (83) | sequence | ALLOW (0) | sendable | Good | $0.175116 | 149.91s |
| 25 | New Relic | newrelic.com | ok | continue (68) | sequence | ALLOW (6) | sendable | Average | $0.174468 | 149.34s |
| 26 | Developer Labs AI | developerlabs.ai | skip |  () |  |  () | research_failed | Unusable | $0.012129 | 29.74s |
| 27 | RocketHub | rockethub.com | ok | continue (68) | draft | ALLOW (0) | sendable | Average | $0.080421 | 102.53s |
| 28 | Netguru | netguru.com | ok | continue (67) | draft | ALLOW (12) | sendable | Average | $0.047979 | 52.49s |
| 29 | Ema | ema.ai | ok | high_priority (95) | sequence | ALLOW (0) | sendable | Good | $0.107943 | 103.46s |
| 30 | Digital Samba | digitalsamba.com | ok | high_priority (81) | sequence | ALLOW (6) | sendable | Good | $0.133158 | 133.38s |
| 31 | Decktopus | decktopus.com | ok | high_priority (82) | sequence | ALLOW (0) | sendable | Average | $0.243309 | 188.17s |
| 32 | Codebenders | codebenders.ai | ok | research_more (26) |  |  () | qualification_blocked | Unusable | $0.016608 | 28.25s |
