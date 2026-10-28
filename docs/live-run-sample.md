# Live run sample (sanitized)

- **Date:** 2026-10-28 (America/New_York)
- **Provider:** NVIDIA API (`--provider nvidia`, `https://integrate.api.nvidia.com/v1`, OpenAI-compatible tool calling)
- **Model:** `nvidia/nemotron-3-ultra-550b-a55b`
- **Command:** `lgnemo check stg_orders.amount --change "switch to integer cents" --provider nvidia`
- **Wall time:** 162 s for the whole agent loop (3 live runs today: 270 s, 228 s, 162 s)

No API keys appear in this file. The Nebius Token Factory path (`--provider nebius`) uses the identical code;
see the README for why this demo ran on NVIDIA's endpoint.

## Tool calls chosen by Nemotron

| # | tool | contract | status | violations | SQL written by the model |
|---|---|---|---|---|---|
| 1 | `impact` |  | ok |  | `` |
| 2 | `warehouse_schema` |  | ok |  | `` |
| 3 | `run_check` | test:not_null_stg_orders_amount | pass | 0 | `SELECT * FROM stg_orders WHERE amount IS NULL` |
| 4 | `run_check` | test:accepted_range_stg_orders_amount | fail | 2 | `SELECT * FROM stg_orders WHERE amount < 0 OR amount > 10000` |
| 5 | `owners` |  | ok |  | `` |
| 6 | `run_check` | contract:dim_customer_grain | pass | 0 | `SELECT customer_id, COUNT(*) as cnt FROM dim_customer GROUP BY customer_id HAVING COUNT(*) > 1` |
| 7 | `run_check` | contract:orders_amount | pass | 0 | `SELECT * FROM fct_orders WHERE order_amount < 0 OR order_amount > 1000000` |

Steps 6–7 came after the agent loop's coverage reminder (it had finished without checking the `contract:` ids from `impact`).
Step 4 caught the planted cents-vs-dollars defect: **2 violating rows**.

## Agent summary (verbatim, trimmed)

## LineageGate Impact Report: `stg_orders.amount` → integer cents (Final)

### 🎯 Blast Radius (27 assets)
**Lineage paths from `column:stg_orders.amount`:**
- **Direct**: `model:stg_orders`, `test:not_null_stg_orders_amount`, `test:accepted_range_stg_orders_amount`
- **1 hop**: `column:int_order_revenue.gross_revenue`
- **2 hops**: `column:int_order_revenue.net_revenue`, `column:int_payments_reconciled.expected_amount`, `column:fct_orders.order_amount`
- **3+ hops**: `int_customer_orders.lifetime_spend`, `int_payments_reconciled.variance`, `fct_revenue_daily.revenue`, `dim_customer.lifetime_value`, `mart_cash_reconciliation.variance_usd`, `mart_finance_revenue.revenue_usd`, `mart_marketing_roi.roas`, `mart_customers.lifetime_value`, `mart_customer_ltv.ltv`
- **Models**: `int_order_revenue`, `fct_orders`, `int_customer_orders`, `int_payments_reconciled`, `fct_revenue_daily`, `dim_customer`, `mart_cash_reconciliation`, `mart_finance_revenue`, `mart_marketing_roi`, `mart_churn_features`, `mart_customer_ltv`, `mart_customers`, `mart_support_health`
- **Dashboard**: `exec.kpi_overview`

### ✅ Contract Checks (All Completed)

| Contract | Check | Status | Violations | Sample |
|----------|-------|--------|------------|--------|
| `test:not_null_stg_orders_amount` | `not_null_stg_orders_amount` | **PASS** | 0 | — |
| `test:accepted_range_stg_orders_amount` | `accepted_range_stg_orders_amount` | **FAIL** | 2 | `ord_0002: 39529`, `ord_0016: 11426` |
| `contract:dim_customer_grain` | `dim_customer_grain` | **PASS** | 0 | — |
| `contract:orders_amount` | `orders_amount` | **PASS** | 0 | — |

**Note**: Freshness/SLA contracts on downstream models were not SQL-checkable and are skipped.

### 🔍 Critical Finding
The **accepted_range** test expects `amount ≤ 10000` (presumably USD), but two orders have values **39,529** and **11,426**.  
Raw tables (`raw__shopify_orders.total_price_cents`, `raw__stripe_payments.amount_cents`) are already in **cents**.  
This strongly suggests `stg_orders.amount` is **already stored in cents**, and the test threshold is wrong (should be ~1,000,000 cents for $10k).

The `contract:orders_amount` check on `fct_orders.order_amount` passes with a 1,000,000 threshold, confirming downstream models already expect cents.

### 👥 Owners to Notify
- **@priya** (Priya Raman, on-call) — `person:priya.raman`
- **@marcus** (Marcus Lee) — `team:finance_analytics`

### 🚦 Recommendation: **HOLD**
**Do not ship** until:
1. **Confirm current unit** of `stg_ord
