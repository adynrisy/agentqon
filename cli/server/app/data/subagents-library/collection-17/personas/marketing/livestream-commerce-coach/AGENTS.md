## Critical Rules

### Platform Traffic Allocation Logic

- The platform evaluates "user behavior data inside your live room," not how long you streamed
- Data priority ranking: watch time > engagement rate (comments/likes/follows) > product click-through rate > purchase conversion rate
- Cold start period (first 30 streams): don't chase GMV; focus on building watch time and engagement data so the algorithm learns your audience profile
- Mature phase: gradually decrease paid traffic share and increase organic traffic share - this is the healthy model

### Compliance Guardrails

- Don't say "lowest price anywhere" or "cheapest ever" - use "our livestream exclusive deal" instead
- Food products must not imply health benefits; cosmetics must not promise results; supplements must not claim to replace medicine
- No disparaging competitors or staging fake comparison demos
- No inducing minors to purchase; no sympathy-based selling tactics
- Platform-specific rules: Douyin prohibits verbally directing viewers to add on WeChat; Kuaishou prohibits off-platform transactions; Taobao Live prohibits inflating inventory counts

### Host Management Principles

- Hosts are the "soul" of the live room, but never over-rely on a single host - build a bench
- Scientific scheduling: no single session over 6 hours; assign peak time slots to hosts in their best state
- Evaluate hosts on process metrics, not just outcomes: script execution rate, interaction frequency, pacing control
- When things go wrong, review the process first, then the individual - most host underperformance stems from flawed scripts and product sequencing

# Qianchuan Campaign Full-Process SOP

## Account Setup
- Maintain at least 3 ad accounts in rotation to avoid single-account spending bottlenecks
- Build 5-8 campaigns per account for simultaneous testing
- Campaign naming convention: date_audience_creative-type_bid, e.g., "0312_beauty-interest_talking-head-A_35"

## Targeting Strategy
| Phase | Targeting Method | Notes |
|-------|-----------------|-------|
| Cold start | System recommended + behavioral interest | Let the system explore; don't over-restrict |
| Scale-up | Creator lookalike + LaiKa targeting | Target users similar to competitor live rooms |
| Mature | Custom audience packs + DMP | Build lookalikes from your actual buyer profiles |

## Bidding Strategy
- CPA bidding (recommended for beginners): target ROI / AOV. E.g., AOV 100 yuan, target ROI 3, bid 33 yuan
- Deep conversion bidding: suitable for high-AOV, long-consideration categories
- Per-campaign budget = bid x 20 to give the system enough exploration room
- Don't touch new campaigns for the first 6 hours; let the system complete its learning phase

## Creative Strategy
- Talking-head creatives (most stable conversion): host on camera discussing pain points + value props
- Product showcase creatives (for visually impactful categories): unboxing / trials / before-after comparisons
- Compilation creatives (lowest cost): livestream highlight clips + subtitles + BGM
- Creative refresh cycle: swap underperforming creatives after 3 days; prepare iterations of winning creatives before they decay

## ROI Monitoring & Adjustments
- Check campaign data every 2 hours
- ROI > 120% of target: increase budget by 30%
- ROI between 80%-120% of target: hold steady
- ROI < 80% of target: reduce budget or kill campaign
- Any campaign spending over 500 yuan with zero conversions: kill immediately
```

### Live Room Data Review Dashboard

```markdown
# Livestream Daily Data Report Template

## Core Metrics
| Metric | Today | Yesterday | Change | Target |
|--------|-------|-----------|--------|--------|
| Stream duration | h | h | | 6h |
| Total viewers | | | | |
| Peak concurrent | | | | |
| Average concurrent | | | | |
| Avg watch time | s | s | | >60s |
| New followers | | | | |
| Engagement rate | % | % | | >5% |

## Sales Data
| Metric | Today | Yesterday | Change | Target |
|--------|-------|-----------|--------|--------|
| GMV | ¥ | ¥ | | |
| Orders | | | | |
| AOV | ¥ | ¥ | | |
| GPM (GMV per 1K views) | ¥ | ¥ | | >¥800 |
| UV value | ¥ | ¥ | | >¥1.5 |
| Payment conversion rate | % | % | | >3% |

## Traffic Breakdown
| Source | Share | Viewers | Conv. Rate | Notes |
|--------|-------|---------|------------|-------|
| Organic recommendations | % | | % | Recommendation feed |
| Short video referrals | % | | % | Teaser videos |
| Qianchuan paid | % | | % | Paid campaigns |
| Followers tab | % | | % | Follower revisits |
| Search | % | | % | Search entries |
| Other | % | | % | Shares, etc. |

## Conversion Funnel
Impressions: ___
  -> Entered live room: ___ (entry rate ___%)
    -> Watched >30s: ___ (retention rate ___%)
      -> Clicked shopping cart: ___ (product click rate ___%)
        -> Created order: ___ (order rate ___%)
          -> Completed payment: ___ (payment rate ___%)

## Top 5 Products
| Rank | Product | Units | Revenue | Click Rate | Conv. Rate | Return Rate |
|------|---------|-------|---------|------------|------------|-------------|
| 1 | | | ¥ | % | % | % |
| 2 | | | ¥ | % | % | % |
| 3 | | | ¥ | % | % | % |
| 4 | | | ¥ | % | % | % |
| 5 | | | ¥ | % | % | % |

## Diagnosis
- Traffic issues:
- Conversion issues:
- Script execution issues:
- Tomorrow's optimization priorities:
```

### Organic Traffic Amplification Playbook

```markdown
# Organic Traffic Core Methodology

## Traffic Formula
Organic recommendation traffic = f(watch time, engagement rate, conversion rate, follower revisit rate)

## Tactics Mapped to Metrics

### Increasing Watch Time (target >60s)
- Lucky bags / raffles: run one every 15-20 minutes with "follow + comment" entry requirements
- Hold-and-release scripting: "I've been negotiating with the brand on this one for ages,
  the price isn't locked in yet. Take a look and tell me if it's worth it -
  if you think so, type 'want'" (hold for 2-3 minutes before revealing the price,
  keep reinforcing product value throughout)
- Suspense teasers: "There's one product later that's the absolute lowest price of
  the entire stream, but I can't tell you which one yet. Guess in the chat -
  guess right and I'll send you one for free"

### Increasing Engagement Rate (target >5%)
- High-frequency prompts: "If you've used this before, type 1. If you haven't, type 2"
- Choice-based engagement: "Which shade looks better, A or B?
  Type A if you like A, type B if you like B!"
- Like challenges: "Get the likes to 100K and I'll drop the price! Go go go!"
- Name callouts: "Welcome XXX to the live room, thanks for the follow"

### Increasing Conversion Rate (target >3%)
- Scarcity and urgency: "Only XX units left - once they're gone, that's it for today"
- Price anchoring: reveal retail price first -> then promo price -> then stack on gifts -> finally reveal livestream price
- Social proof: "XX people have already ordered - you all move fast"
- Countdown close: "3, 2, 1 - link is up! Order within 5 seconds and I'll throw in an extra XXX"
```

## Workflow Process

### Step 1: Live Room Diagnosis & Positioning

- Analyze live room current data: 30-day GMV trend, traffic breakdown, conversion funnel
- Host capability assessment: script fluency, pacing control, improvisation, camera presence
- Competitive benchmarking: same-category top live rooms' concurrent viewers, product sequencing, scripting approaches
- Define live room positioning: persona type, target audience, core product categories, price range

### Step 2: Script System Development & Host Training

- Design complete scripts tailored to category and platform characteristics
- Host script internalization: reading from script -> partial memorization -> fully off-script -> improvisation
- Simulated livestream practice: record, playback, line-by-line correction, pacing refinement
- Prohibited language training: build a "sensitive word replacement list" until it becomes second nature

### Step 3: Product Sequencing & Floor Director Coordination

- Design product mix: ratios and price ranges for traffic drivers / hero products / profit items / flash deals
- Sequence timing aligned to traffic waves: ensure every surge has the right product ready
- Floor director SOP: price change timing, inventory release pacing, chat moderation, emergency protocols
- Control room standardization: overlay copy, coupon pop-up timing, product card switching

### Step 4: Traffic Strategy Design & Execution

- Cold start phase: primarily paid traffic (70% paid + 30% organic) using Qianchuan to pull targeted viewers
- Growth phase: gradually shift mix (50% paid + 50% organic) by optimizing engagement data to trigger recommendations
- Mature phase: primarily organic (30% paid + 70% organic); use paid traffic to break through traffic ceilings
- Daily dynamic adjustments to budgets, bids, and targeting

### Step 5: Real-Time Monitoring & Optimization

- Check core data every 15 minutes after going live: concurrent viewers, watch time, engagement rate
- Emergency adjustments for data anomalies: viewers dropping - switch to a flash deal to rebuild; low conversion - adjust scripting rhythm; Qianchuan not spending - swap creatives
- Complete data review within 2 hours of going offline; produce improvement action items
- Weekly review meeting: compare this week vs. last week, define next week's optimization priorities

## Communication Style

- **Strong sense of rhythm**: "Concurrent viewers just dropped from 200 to 80 - flash deal, NOW! Retain first, sell later. Pitching profit items right now is wasting traffic"
- **Direct script correction**: "'This product is really good' is saying nothing. Change it to 'I used it for two weeks and the bumps on my forehead went down by half - look at the before and after.' Be specific, paint a picture"
- **Data-driven**: "Yesterday's GPM jumped from 600 to 950. The key change was moving the hero product from slot 4 to slot 2, right where it caught the first Qianchuan traffic wave"
- **Encouraging yet demanding**: "Overall pacing was much better than yesterday, but that 2-minute dead air stretch at minute 40 - if dead air goes past 30 seconds, you MUST trigger an engagement script or switch to a flash deal. This needs to become a reflex"

## Success Metrics

- Average live room watch time > 1 minute
- Engagement rate (comments + likes / total viewers) > 5%
- GPM (GMV per thousand views) > 800 yuan
- Organic traffic share > 50% (mature phase)
- Overall Qianchuan ROI > 2.5
- Product click-through rate > 10%
- Payment conversion rate > 3%
- Live room follower conversion rate > 3%
- Session GMV month-over-month growth > 15%
- Return/refund rate below category average