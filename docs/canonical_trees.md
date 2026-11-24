# Canonical Breakdown trees

**Status:** 2026-11-20. Eval-worlds spec §5 item 2. One tree per company in
`examples/breakdown_<company>_tree.yml`, the trees Breakdown's evaluation
scores against (Breakdown `knowledge/evaluation_spec.md` §4.3). This note says
what each tree is for, why it has its shape, which scenarios the world sets
now plant, and what is still missing.

## Rules every tree follows

- **Every fetched node binds to a generator metric by name**: the last part
  of its `source`. A world set serves the tree with no bindings file, and
  `scripts/verify_consumers.py` checks each `source` against `mf query`.
- **Formula where an identity exists, learned where the relation is
  behaviour.** Revenue = volume × price per unit, bookings = wins × deal size,
  net = gross − deductions are formulas; one funnel stage into the next, ad
  spend into traffic, sell-through into reorders are learned edges.
- **Fan-in stays small.** No formula has more than three parents
  (Breakdown's Shapley cap is ten); no learned node has more than two.
- **Price and mix are answered by slicing a rate, not by per-member nodes.**
  Breakdown splits a sliced rate's change into `within` (the member's own
  rate moved) and `mix` (weight moved between members). A mix shift moves no
  member total in the direction a per-member tree would need (tees units
  down 25%, every other category up, total exactly unchanged), so a node per
  category would show offsetting gaps and no cause; the rate's `mix` term is
  the answer. Every tree therefore carries a price-per-unit rate with the
  dimension the mix lives in.
- **Each tree has a rate, a stock, a learned edge, a formula and a sliceable
  dimension** (spec §5 item 2).
- `breakdown check` passes on all four (run from the breakdown repo with the
  `dbt` extra's `mf` on `PATH`; the trees use the `local` provider, because
  the `dbt` provider refuses cumulative metrics such as `customer_base`).
  The provider's `project_path` is relative, so run it from `examples/`,
  with each dbt project parsed (`dbt parse` writes the
  `target/semantic_manifest.json` the stock check reads).

## Alpenglow (retail_dtc)

**Questions.** Why did net revenue drop: traffic, conversion, basket, price,
discounts or returns? Which channel, device, country? Revenue up with units
flat: price or mix? Is the margin decline price or mix? Which category's
returns?

**Shape** (18 nodes; daily funnel, weekly returns and apex):
`marketing_spend → sessions`; `converting_sessions = sessions ×
conversion_rate`; `customer_base → returning_customer_orders`; `orders =
converting + returning`; `orders → units`; `gross_revenue = units ×
avg_unit_price`; `discounted_orders → discounts`; `returned_units = units ×
return_rate`, `→ refunds`; `net_revenue = gross − discounts − refunds`;
`gross_margin = gross_revenue × margin_rate`.

**Why.** It keeps the spine of Breakdown's benchmark tree (which grew the old
nine-flow chain into rates, a stock and identities) with two changes. Every
node binds by name, so the four channel-session nodes that needed a bindings
file became one `sessions` node sliced by channel. And revenue is
units × price per unit, not orders × AOV, because the category mix lives in
order lines: a category-demand shift moves neither orders nor total units.

**The tees fix.** `tees_demand_slump` was rooted at `units[category=tees]`
and the tree had only total `units`, which it moves by exactly zero: the root
landed on a node the anomaly never moved. Two changes:

1. The generator gained `item_revenue` and `avg_unit_price` (item revenue /
   units) on the order-line model, sliceable by category, and the tree
   carries `avg_unit_price` with a `category` dimension.
2. Ground truth gained `root_metric_mix`: for a mix-shift driver, the ratio
   whose total it moves (`category_demand.* → avg_unit_price`), next to the
   unchanged root slice. It is the mix analogue of `root_metric_rate`.

Measured on the three-seed set (`out/worlds/canon_alpenglow_retail_dtc`):
`avg_unit_price` total +4.6% (established at 10%), while every category's
own price per unit moves by under 0.5% (none established) and units move
−25.5% at tees and +6 to +9% in every other category, total exactly zero.
So the whole change in `avg_unit_price` is `mix`, located at tees, and the
root now has a node the anomaly moves. The list-price scenario added beside
it is the contrast: `pants_price_increase` moves avg_unit_price[pants] by
+12.0% (`within`) and pants units −13.9% (`mix`).

Verified with Breakdown's runner loader (read-only, from its worktree): all
18 nodes are served from the series file with no bindings. The runner does
not read `root_metric_mix` yet, so today it still maps the tees root to
`units`; it needs the same rule it has for `root_metric_rate` (below).

## Meridian (b2b_services)

**Questions.** Why did bookings drop: fewer wins or smaller deals? Fewer
wins because the top dried up or a stage stopped converting, which stage,
industry, source? Is the cycle stretching; are deals stalling (open pipeline
up) or not arriving (down)? Bookings held but invoicing fell: is delivery
slipping? Is cash keeping up?

**Shape** (15 nodes, weekly): `leads → qualified_deals → proposals_sent →
negotiations → deals_won` (learned, lags 0/1/2/1 weeks, priors at the
configured advance rates); `sales_cycle_days → deals_won`; `bookings =
deals_won × avg_deal_size`; `leads → deals_lost`; `closed_deals = won +
lost`; `win_rate = won / closed`; `leads, sales_cycle_days → open_deals →
open_pipeline_value` (stocks); `bookings → invoiced_revenue →(4 weeks)
collected_cash`.

**Why.** Weekly, because a few deals close a day, none at weekends, each a
lognormal amount. The funnel is learned with a lag per stage because stage
entries are dated by event and a stage takes days to weeks; the coefficients
are the stage conversion rates. Win rate is a close-date ratio (a lead
drought briefly raises it), so it is a sink to read beside the funnel. The
open pipeline answers "stall or drought". Invoicing has one lag per parent,
so the edge carries the 40% invoiced at signing and the delivery balance sits
in the level: a delivery slowdown shows as an innovation at
`invoiced_revenue` with bookings unmoved.

## Bristlecone (cpg_wholesale)

**Questions.** Net sales down: volume or price per case; price within a
category or mix? Shipments down because shoppers bought less, or because a
retailer destocked? Which retailer or category lost consumption? Promotion or
advertising?

**Shape** (9 nodes, weekly): `trade_spend, marketing_spend → pos_units →
pos_dollars`; `pos_units →(1 week) shipped_cases`; `shipped_cases, pos_units
→ trade_inventory_build` (stock); `wholesale_net_revenue = shipped_cases ×
avg_case_price`; `wholesale_gross_revenue = net + trade_spend`.

**Why.** Weekly because POS is. Shipments replenish last week's scans, so
one week's lag is the mechanism. The inventory build is the sell-in less
sell-through stock; it sits under both flows with a loose local level
carrying last week's balance. Advertising is an edge into POS because that is
what an analyst believes; in this company it is decoupled by construction, so
the right answer is that it explains nothing. Retailers: POS slices by
`retailer_banner`, shipments by `channel_type`, which here name the same
five retailers.

## White Cube (b2c_saas)

Adopted verbatim from Breakdown's `demo/white_cube_tree.yml` (only the
header and provider differ). It is the tree the runner already scores
against, it has been reviewed more than any other, and the old example here
(MRR = ARPU × active subscriptions as apex, new MRR learned from counts) was
weaker on every count. Its config cannot gain anomalies without moving the
pinned golden hashes, so its scenario gaps below stay gaps.

Proposed, not made: carry `mrr` as a stock beside `active_subscriptions`
(today the tree explains flows only, so an analyst asking "why is MRR lower
than last quarter" must sum `net_new_mrr`); and a price node for plans,
which needs a generator driver White Cube does not have.

## Scenario coverage

Each cell names the anomaly. **New** ones were added with this note; the
drivers marked † were added to the generator for them (each exactly 1.0
unless an anomaly moves it, drawing nothing, so every existing raw table is
byte-identical and they stay out of the surprise pool).

| Scenario | Alpenglow | Meridian | Bristlecone | White Cube |
|---|---|---|---|---|
| Pricing change | **pants_price_increase** (price.pants† +12%, permanent) | discounting_creep; **rate_card_increase** (+12%, permanent) | **teas_list_price_increase** (price.teas† +8%, permanent) | none (no price driver) |
| Mix shift | tees_demand_slump | none possible (gap) | tinctures_viral_spike, teas_demand_scare (category mix in avg_case_price) | none |
| Channel / retailer loss | **paid_search_account_suspended** (spend ×0.05, 2 weeks) | **referral_partner_exit** (referral leads ×0.3, permanent) | whole_earth_delisting; **summit_mart_capsules_delisting** (one banner, one category, permanent) | none |
| Sell-in vs sell-through | | | **summit_mart_destock** (ordering.mass† ×0.6) | |
| Bookings vs invoicing | | **delivery_capacity_crunch** (delivery_time_scale† ×1.6) | | |
| Seasonal miss | **holiday_organic_shortfall** (organic ×0.75, Nov–Dec) | **spring_inbound_miss** (website leads ×0.7) | **mild_flu_season** (teas ×0.8, Dec–Feb) | none |
| Data-quality break that moves metrics | **order_items_partial_load**; orders_session_id_nulls | **stage_events_sync_gap**; deals_closed_at_nulls | pos_feed_outage | product_pipeline_outage |
| Off-tree cause | none (see gaps) | none | none | none |
| Slow drift vs step | **organic_conversion_erosion** (ramp to 0.8) | **proposal_conversion_erosion** (ramp to 0.8) | **greenfields_shelf_erosion** (ramp to 0.75) | latam_campaign_spend_ramp |
| Two simultaneous causes | **paid_search_account_suspended + email_checkout_link_bug** (different nodes, same fortnight) | **rate_card_increase + rate_card_pushback** | **summit_mart_destock + tinctures_competitor_launch** | none |
| Decoy (edge that should explain nothing) | | | brand_budget_cut | |

**Sizes**, cross-seed mean relative change over the declared window on the
three-seed sets (`out/worlds/canon_*`, seeds 0–2, null plus every single),
root / top of tree. Top-of-tree weekly noise from spec §9.4: Alpenglow net
revenue 7.3%, Meridian bookings 48%, Bristlecone net sales 7.6%.

| Anomaly | Root | Top |
|---|---|---|
| teas_list_price_increase | avg_case_price[teas] +8.0% | net sales −0.9% (price and volume offset: the point) |
| summit_mart_destock | shipped_cases[mass] −38% | −8.4%; POS exactly unchanged |
| tinctures_competitor_launch | pos_units[tinctures] −25% | −4.3% |
| mild_flu_season | pos_units[teas] −20% | −11.6% |
| summit_mart_capsules_delisting | pos_units[capsules, summit_mart] −98% (total −2.8%) | −2.6% (permanent) |
| greenfields_shelf_erosion | pos_units[greenfields] −11.6% | −2.8% over the ramp (it then holds at 0.75) |
| spring_inbound_miss | leads[website] −30% | bookings −8.9% (open: B2B bookings noise) |
| proposal_conversion_erosion | proposals_sent −8.7% | bookings −4.7% |
| delivery_capacity_crunch | invoiced_revenue −7.0% | bookings exactly unchanged |
| rate_card_increase | avg_deal_size +9.3% | bookings +9.3% |
| rate_card_pushback | deals_won −16.7% | bookings −15.3% |
| referral_partner_exit | leads[referral] −71% | bookings −7.2% (open) |
| pants_price_increase | avg_unit_price[pants] +12.0% (own price; pants units −13.9%, others +2%, total exactly 0) | net revenue +1.1% |
| paid_search_account_suspended | marketing_spend[paid_search] −95% | −7.2% |
| email_checkout_link_bug | conversion_rate[email] −48% | −4.1% |
| holiday_organic_shortfall | sessions[organic] −25% | −3.5% |
| organic_conversion_erosion | conversion_rate[organic] −9.5% over the ramp | −1.0% over the ramp |
| order_items_partial_load (dq) | moves units, item_revenue, avg_unit_price, gross_margin; not gross_revenue | 0 |

What these sizes mean for the §9.4 problem (most stories too small to see
from the top): the new Bristlecone stories are all at or above its weekly
noise except the delisting and the erosion, which are permanent and so
show over longer windows.
Meridian's bookings are so noisy weekly (48%) that only the cycle backlog
and the pushback clear it; that is the business, not the magnitudes, and
the B2B tree is meant to be read at the funnel stage. The price changes are
deliberately small at the top: a price change that volume offsets is the
case a price-volume tree exists to catch.

## What Breakdown's runner must change to use these trees

- **Tree paths.** `--tree <fake_companies>/examples/breakdown_<company>_tree.yml`.
  No `--bindings` file: every node binds by name. The runner's
  `bindings/alpenglow_tree.yml` belongs to its own benchmark tree, not this
  one. The canonical White Cube tree is the runner's demo tree with another
  provider block, so its scores carry over (the tree hash differs).
- **Read `root_metric_mix`** the way it reads `root_metric_rate`: when the
  ratio it names is a non-formula node, the truth is that node
  (`on_tree_mix`). Checked against the runner's own `map_root` with the
  field substituted: the tees slump maps to `avg_unit_price`. Without it the
  runner maps the root to `units`, which the slump moves by exactly zero.
- **Score localization inside a node by slice.** These trees localize price
  versus mix, a retailer, a stage's industry by a node's `dimensions`; the
  runner swaps the provider for `mock`, whose slices are synthetic, so
  nothing below a node is scored today. It needs a slice fetcher over the
  series file (every metric is already exported by every dimension the trees
  declare) and a slice score against `root_slice`.
- **Week values of the stocks** are now MetricFlow's own: the snapshot
  measures are semi-additive and the cumulative stocks declare
  `period_agg: last` (spec §10.10), so `open_deals`, `open_pipeline_value`,
  `trade_inventory_build` and `mrr` come back as their week-end level from
  `mf query` and from the runner's loader alike. Breakdown's C66 stock
  refusal passes on all four trees (`trade_inventory_build` is a warning:
  C66 does not model a cumulative metric's fold, so it cannot verify it).
- **Two causes at once** need a world holding exactly both (spec §10.8 case
  2); until the generator builds one, score the pair on each single world and
  on `--story`.
- **Delivery crunch** onset is about two months after its declared window
  (see the gaps below); the runner's onset rule (first difference, then
  `onset`) should pick it up, but the declared-window effect understates it.

## Known gaps

**Generator metrics a realistic tree wants and the generator lacks:**

- CPG: **distribution** (stores selling, ACV-weighted distribution, points
  of distribution) and **velocity** (units per store per week). Store counts
  are config constants with no table, so "is it distribution or velocity" (the
  first CPG question after "price or volume") cannot be asked; a delisting is
  modelled as velocity or demand, not as fewer doors.
- CPG: **retailer on shipments.** `shipped_cases` slices by `channel_type`,
  not `retailer_banner`; they coincide in this config (one banner per
  channel), and would not with two grocers.
- CPG: **trade spend is ~0.3% of gross**, against 15–25% in real CPG, and it
  exists only during five promotions; base list price per case
  (gross / cases) is not a metric, so the gross-to-net bridge has no rate.
- CPG: **promotion and holiday calendars** are not data (spec §5 item 1); a
  tree cannot declare them as known events except by hard-coding dates.
- B2B: **backlog and utilization.** Contracted-but-undelivered value (a
  stock), delivery capacity and billable hours do not exist; the delivery
  crunch is visible only as late invoices.
- B2B: **stage conversion rates** as metrics (cohort-dated: of deals entering
  a stage in week w, the share that advanced). The tree learns them as edge
  coefficients instead, which is weaker for a rate that changes.
- B2B: **no mix driver.** Lead source and size tier do not change win rate
  or cycle in the generator, so a shift toward small accounts moves deal size
  only through `deal_size`; no mix-shift scenario is possible.
- Retail: price elasticity only **reweights the basket** (lines per order do
  not read price), so a price rise never loses total units; and
  category demand is pure mix, so a warm winter cannot cut total jacket-led
  demand.
- Retail: a `paid_sessions` metric (sessions from paid channels). The spend
  edge regresses all sessions on spend; only the paid share responds.
- All: **cost of goods / COGS changes** (unit costs are fixed), so "is the
  margin decline cost" has no scenario.

**Scenario gaps:**

- **Off-tree causes: none planted.** Root labels name the metric closest to
  the driver, and every driver's closest metric is a node of its canonical
  tree, so no anomaly is off-tree by construction. Off-tree is a property of
  a (tree, anomaly) pair; the honest way to test it is to run the canonical
  tree with the root's node removed (Breakdown's runner already reports
  `off_tree` when the root is not a node). Planting one would mean leaving a
  realistic node out of a canonical tree.
- **Two simultaneous causes are planted, but no world holds exactly both.**
  `worlds --singles` builds one world per anomaly; `--story` builds every
  anomaly at once. A world with exactly the pair needs the `--combos A+B`
  option of spec §10.8 case 2, not built. `generate --anomalies
  only:A,B` builds one by hand today.
- **White Cube** gets nothing new: any change to its config moves the pinned
  B2C golden hashes. Missing there: a plan price change, a mix shift between
  plans, a channel loss, a seasonal miss, two causes at once.
- **The delivery crunch's effect lands after its declared window**: the
  window dates the signings, and their balance invoices fall due about two
  months later. Measured over the declared window it is −7% (established at
  10% on three seeds, not at 1%); score it over the window shifted by the
  delivery time, by the recipe in spec §10.7.

---

*This document is written and maintained by an AI agent (Claude), with human oversight.*
