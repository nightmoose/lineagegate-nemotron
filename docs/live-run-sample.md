# Live run samples (sanitized)

- **Date:** 2026-10-28 (America/New_York)
- **Providers:** Nebius Token Factory (`--provider nebius`, `https://api.tokenfactory.nebius.com/v1`, model `nvidia/nemotron-3-super-120b-a12b`) for run 0; NVIDIA API (`--provider nvidia`, `https://integrate.api.nvidia.com/v1`, OpenAI-compatible tool calling)
- **Model:** `nvidia/nemotron-3-ultra-550b-a55b`
- No API keys appear in this file. Runs 1–2 below used NVIDIA's endpoint, the alternate host; run 0 is the Token Factory live demo.

Both planted defects were caught live, each by its own command (they sit on different lineage branches).

## 0. Token Factory: `lgnemo check stg_orders.amount --change "switch to integer cents" --provider nebius` (40 s)

Nemotron Super on Nebius Token Factory: 9 tool calls, 12 model calls, 79,915 tokens (75,381 prompt / 4,534 completion).
Verdict: **HOLD** — the switch to cents makes every downstream dollar field 100× too large; notify @priya (on-call) and @marcus
and the `finance_analytics` team. Super ran the null, grain and non-negative checks below; the `accepted_range` test (which
surfaces the planted cents-vs-dollars rows) was not run in this pass. Ultra's run 1 below runs it and flags the 2 rows.

| # | tool | contract | status | rows | SQL written by the model |
|---|---|---|---|---|---|
| 1 | `impact` |  | ok |  | `` |
| 2 | `owners` |  | ok |  | `` |
| 3 | `impact` |  | ok |  | `` |
| 4 | `impact` |  | ok |  | `` |
| 5 | `warehouse_schema` |  | ok |  | `` |
| 6 | `run_check` | test:not_null_stg_orders_amount | pass | 0 | `SELECT * FROM stg_orders WHERE amount IS NULL;` |
| 7 | `run_check` | contract:dim_customer_grain | pass | 0 | `SELECT * FROM dim_customer GROUP BY customer_id HAVING COUNT(*) > 1;` |
| 8 | `run_check` | contract:orders_amount | pass | 0 | `SELECT * FROM stg_orders WHERE amount < 0;` |
| 9 | `run_check` | contract:dim_customer_grain | pass | 0 | `SELECT * FROM mart_customers GROUP BY customer_id HAVING COUNT(*) > 1;` |

## 1. `lgnemo check stg_orders.amount --change "switch to integer cents" --provider nvidia` (302 s)

The planted **cents-vs-dollars defect: caught (2 rows)** by both the dbt range test and `contract:orders_amount` on `stg_orders`.
Steps 11–12 came after the loop's coverage reminders: `impact` lists the tables each contract governs, and the reminder
named `mart_customers` and `stg_orders`, which the model hadn't checked yet. Steps 6–9 are exploratory SELECTs the model sent through
`run_check`, so their row counts are samples, not violations.

| # | tool | contract | status | rows | SQL written by the model |
|---|---|---|---|---|---|
| 1 | `impact` |  | ok |  | `` |
| 2 | `warehouse_schema` |  | ok |  | `` |
| 3 | `owners` |  | ok |  | `` |
| 4 | `run_check` | test:not_null_stg_orders_amount | pass | 0 | `SELECT * FROM stg_orders WHERE amount IS NULL` |
| 5 | `run_check` | test:accepted_range_stg_orders_amount | fail | 2 | `SELECT * FROM stg_orders WHERE amount < 0 OR amount > 10000` |
| 6 | `run_check` | test:accepted_range_stg_orders_amount | fail | 10 | `SELECT gross_revenue FROM int_order_revenue LIMIT 10` |
| 7 | `run_check` | test:accepted_range_stg_orders_amount | fail | 10 | `SELECT order_id, total_price_cents FROM raw__shopify_orders LIMIT 10` |
| 8 | `run_check` | test:accepted_range_stg_orders_amount | fail | 20 | `SELECT order_id, amount FROM stg_orders LIMIT 20` |
| 9 | `run_check` | test:accepted_range_stg_orders_amount | fail | 50 | `SELECT order_id, total_price_cents FROM raw__shopify_orders` |
| 10 | `run_check` | contract:dim_customer_grain | pass | 0 | `SELECT customer_id, COUNT(*) as cnt FROM dim_customer GROUP BY customer_id HAVING COUNT(*) > 1` |
| 11 | `run_check` | contract:orders_amount | pass | 0 | `SELECT order_id, order_amount FROM fct_orders WHERE order_amount < 0 OR order_amount > 10000` |
| 12 | `run_check` | contract:dim_customer_grain | pass | 0 | `SELECT customer_id, COUNT(*) as cnt FROM mart_customers GROUP BY customer_id HAVING COUNT(*) > 1` |
| 13 | `run_check` | contract:orders_amount | fail | 2 | `SELECT order_id, amount FROM stg_orders WHERE amount < 0 OR amount > 10000` |

### Agent summary (trimmed)

## Final Impact Report: `stg_orders.amount` → integer cents

### Blast Radius (27 assets)
**Lineage paths from `column:stg_orders.amount`:**
1. `column:int_order_revenue.gross_revenue` → `column:int_order_revenue.net_revenue` → `column:int_payments_reconciled.expected_amount` → `column:int_payments_reconciled.variance` → `column:mart_cash_reconciliation.variance_usd`
2. `column:int_order_revenue.gross_revenue` → `column:fct_orders.order_amount` → `column:fct_revenue_daily.revenue` → `column:mart_finance_revenue.revenue_usd` / `column:mart_marketing_roi.roas`
3. `column:int_order_revenue.gross_revenue` → `column:int_order_revenue.net_revenue` → `column:int_customer_orders.lifetime_spend` → `column:dim_customer.lifetime_value` → `column:mart_customers.lifetime_value` / `column:mart_customer_ltv.ltv`
4. Models: `stg_orders`, `int_order_revenue`, `int_payments_reconciled`, `fct_orders`, `int_customer_orders`, `fct_revenue_daily`, `dim_customer`, `mart_cash_reconciliation`, `mart_finance_revenue`, `mart_marketing_roi`, `mart_churn_features`, `mart_customer_ltv`, `mart_customers`, `mart_support_health`
5. Dashboard: `exec.kpi_overview`

### Current State (from warehouse)
- **`raw__shopify_orders.total_price_cents`** — *misnamed*: stores **dollars** (e.g., 165.3, 327.21)
- **`stg_orders.amount`** — stores **dollars** (e.g., 150.02, 39529)
- **`int_order_revenue.gross_revenue`** — stores **dollars** (e.g., 315.74)
- All downstream models expect **dollars**

### Contract Checks (all SQL-checkable contracts now run)

| Contract | Table | Check | Status | Violations | Notes |
|----------|-------|-------|--------|------------|-------|
| `test:not_null_stg_orders_amount` | `stg_orders` | `amount IS NULL` | **PASS** | 0 | No nulls in staging |
| `test:accepted_range_stg_orders_amount

## 2. `lgnemo check stg_customers.customer_id --change "merge CRM and Shopify customer ids" --provider nvidia` (126 s)

The planted **duplicate `customer_id`: caught** (`contract:customers_identity` on `stg_customers`, 2 rows = the duplicated pair). Recommendation: HOLD.

| # | tool | contract | status | rows | SQL written by the model |
|---|---|---|---|---|---|
| 1 | `impact` |  | ok |  | `` |
| 2 | `warehouse_schema` |  | ok |  | `` |
| 3 | `run_check` | contract:customers_identity | fail | 2 | `SELECT customer_id FROM stg_customers WHERE customer_id IS NULL OR customer_id IN ( SELECT customer_id FROM stg_customers GROUP BY customer_id HAVING COUNT(*) >` |
| 4 | `run_check` | contract:customers_identity | pass | 0 | `SELECT customer_id FROM raw__crm_customers WHERE customer_id IS NULL OR customer_id IN ( SELECT customer_id FROM raw__crm_customers GROUP BY customer_id HAVING ` |
| 5 | `run_check` | contract:dim_customer_grain | pass | 0 | `SELECT customer_id FROM dim_customer WHERE customer_id IS NULL OR customer_id IN ( SELECT customer_id FROM dim_customer GROUP BY customer_id HAVING COUNT(*) > 1` |
| 6 | `run_check` | contract:dim_customer_grain | pass | 0 | `SELECT customer_id FROM mart_customers WHERE customer_id IS NULL OR customer_id IN ( SELECT customer_id FROM mart_customers GROUP BY customer_id HAVING COUNT(*)` |
| 7 | `owners` |  | ok |  | `` |

### Agent summary (trimmed)

## LineageGate Impact Report

### 🎯 Blast Radius (22 downstream assets)
**Lineage paths from `stg_customers.customer_id`:**
- **Direct**: `dim_customer.customer_id` → `stg_customers` model → `test:unique_stg_customers_customer_id`
- **Via dim_customer**: `mart_customers.customer_id`, `mart_support_health.tickets_per_customer`, `fct_orders`, `mart_churn_features`, `mart_customer_ltv`, `mart_marketing_roi`, `fct_revenue_daily`, `mart_finance_revenue`
- **Dashboards**: `exec.kpi_overview`, `crm.customer_360`, `cx.support_health`, `growth.marketing_roi`, `finance.revenue_daily`
- **Tests**: `unique_mart_customers_customer_id`, `freshness_mart_finance_revenue`

### 📋 Contracts & Check Results

| Contract | Spec | Applies To | Check | Status | Violations |
|----------|------|------------|-------|--------|------------|
| `contract:customers_identity` | customer_id unique & not null; sourced from CRM | `raw__crm_customers`, `stg_customers` | `stg_customers_customer_id_unique_not_null` | **FAIL** | 2 (duplicate `cus_0004`) |
| `contract:customers_identity` | same | `raw__crm_customers` | `raw_crm_customers_customer_id_unique_not_null` | **PASS** | 0 |
| `contract:dim_customer_grain` | exactly 1 row per customer_id | `dim_customer`, `mart_customers` | `dim_customer_customer_id_unique_not_null` | **PASS** | 0 |
| `contract:dim_customer_grain` | same | `mart_customers` | `mart_customers_customer_id_unique_not_null` | **PASS** | 0 |
| `contract:finance_revenue_freshness` | refreshed by 07:00 ET daily | `mart_finance_revenue` | *skipped (freshness SLA, not SQL-checkable)* | — | — |

### 👥 Owners to Notify
- **Team**: `team:customer_analytics`
- **On-call**: `@jules` (Jules Chen)
- **Also**: `@ana` (Ana Petrova)

### ⚠️ Ship / Hold Recommendation: **HOLD**

**Reason**: The proposed me
