# Standard Operating Procedures

## SOP-01: Single Keyword Product Research

```
┌──────────────────────────────────────────────────┐
│  Input: Keyword + Target Site + Filters + Cost    │
└──────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────┐
│  Step 1: Drive Browser                            │
│  - Open target platform (Amazon/TikTok/eBay)      │
│  - Auto-enter keyword search                      │
│  - Select target site/region                      │
│  - Apply filters (price, rating, shipping)        │
└──────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────┐
│  Step 2: Data Collection                          │
│  - Scrape TOP N products (default TOP 50)         │
│  - Extract: Title, Price, Sales/Rank, Rating,     │
│    Reviews, URL, Seller, Brand, FBA/FBM,          │
│    Listing Date, Category Path                    │
└──────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────┐
│  Step 3: Data Cleaning & Validation               │
│  - Deduplicate / merge variants                   │
│  - Standardize currency                           │
│  - Handle missing values (mark as estimated)      │
│  - Outlier detection (price/sales anomalies)      │
│  - Abort if missing rate >30%                     │
└──────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────┐
│  Step 4: Profit Model Calculation                 │
│  - Fetch sourcing price (1688/Alibaba/user input) │
│  - Platform fees (Amazon 15% / TikTok 5%)         │
│  - FBA/logistics (dimensional weight estimate)    │
│  - Ad cost (estimated 10-20%)                     │
│  - Output: Gross Profit / Margin / Break-even     │
└──────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────┐
│  Step 5: Multi-dimensional Analysis               │
│  ├─ Demand: sales distribution, trends, season    │
│  ├─ Competition: seller count, brand concentration│
│  ├─ Pricing: price range, strategies              │
│  ├─ Reviews: positive keywords, pain points, gaps │
│  └─ Compliance: certifications (CE/FDA/CPC)       │
└──────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────┐
│  Step 6: Six-Dimension Scoring & Decision         │
│  - Score 0-100 across 6 dimensions                │
│  - Risk level: High / Medium / Low                │
│  - Verdict: ✅ Go / ⚠️ Caution / ❌ No-Go         │
│  - Decision rationale + alternatives              │
└──────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────┐
│  Step 7: Report Output                            │
│  - Excel report (5 Sheets)                        │
│  - Markdown decision summary                      │
│  - Risk warning checklist                         │
└──────────────────────────────────────────────────┘
```

## SOP-02: Multi-Keyword Batch Analysis

```
Input: Keyword list + unified filters + cost params
    ↓
Loop SOP-01 for each keyword
    ↓
Consolidated comparison: opportunity ranking + profit comparison
    ↓
Output: Comprehensive Excel + Top 3 recommendations
```

## SOP-03: In-depth Competitor Comparison

```
Input: Competitor ASIN/link list (2-5 items)
    ↓
Step 1: Collect detailed data per competitor
    ↓
Step 2: Comparison matrix (price/sales/reviews/USP)
    ↓
Step 3: Differentiation opportunity (unmet pain points)
    ↓
Step 4: Pricing strategy recommendations
    ↓
Output: Competitor report + differentiation positioning
```

## Exception Handling

| Exception | Handling | User Notification |
|-----------|----------|-------------------|
| Page load failure | Retry 3x, 5s interval | "Target site slow, retrying..." |
| Anti-scraping (captcha) | Pause, use cached data | "Anti-bot triggered, switching to cache" |
| Selector invalid (site update) | Mark error, skip field | "Some fields unavailable due to site update" |
| Data missing >30% | Abort analysis | "Incomplete data, suggest changing keyword" |
| Sourcing price failed | Use user input or mark pending | "Sourcing price pending, please input manually" |

## Interaction Methods

### Guided Conversation (Recommended)

```
Agent: "Hi! I'm your product research assistant. Tell me:

1️⃣ Target platform? (Amazon / TikTok Shop / eBay / Shopee)
2️⃣ Target site? (US / DE / JP / UK / ...)
3️⃣ Keyword to analyze? (e.g., yoga mat)
4️⃣ Your sourcing cost range? (optional)
5️⃣ Target selling price range? (optional)
6️⃣ Analysis depth? (Quick TOP20 / Standard TOP50 / Deep TOP100)"

User: "Amazon US, yoga mat, cost $8-12, price $25-40, standard"

Agent: "✅ Received! Analyzing TOP 50 for 'yoga mat' on Amazon US.
   Cost: $8-12, Price: $25-40.
   Estimated time: 3-5 minutes. Start? (y/n)"
```

### Quick Commands (Expert Mode)

```
/analyze Amazon US yoga mat --cost 8-12 --price 25-40 --top 50
```

### Batch Analysis

```
User: "Batch analyze DE kitchen: küchenmesser, schneidebrett, kochtopf, pfanne"

Agent:
1. Analyze 4 keywords sequentially
2. Generate consolidated comparison
3. Rank by overall score
4. Output: "Top 3: 1.küchenmesser 2.pfanne 3.kochtopf"
```

## Important Notes & Limitations

### Data Limitations
1. **Sales**: Some platforms don't disclose sales; BSR estimates have error margins
2. **Sourcing prices**: 1688 prices are reference only; actual depends on negotiation
3. **FBA fees**: Estimated by standard sizes; actual varies by warehouse
4. **Trends**: Based on historical data; cannot predict sudden events

### Compliance Reminders
1. Follow platform robots.txt; collection interval >3 seconds
2. Analysis is reference only, not infringement judgment
3. Products must comply with platform prohibited/restricted rules
4. Cross-border sales require understanding target country tax (VAT/sales tax)

### Usage Suggestions
1. **Verify sourcing**: Always get actual supplier quotes before committing
2. **Small batch test**: Even high-score products — validate with 50 units first
3. **Continuous monitoring**: Markets change fast; re-analyze periodically
4. **Multi-source validation**: Combine with social trends and supply chain conditions
