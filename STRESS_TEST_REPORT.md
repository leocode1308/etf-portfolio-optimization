# Stress Test & Quantitative Audit Report

## 1. Risk Analysis & Stress Testing

### 1.1 Liquidity Impact on 5% Concentration Cap
The model enforces a hard cap of 5% (`Max Wt_2 = 0.05`) and a secondary cap of `20 * FCap Wt`. 
*   **Liquidity Risk:** While the `20 * MarketCap` cap provides some protection against overweighting small caps, it does not explicitly account for **Average Daily Volume (ADV)**. In a stressed market, a 5% position in a constituent that represents 0.25% of the market cap (the threshold where 20x becomes 5%) could take weeks to liquidate without significant slippage.
*   **Audit Finding:** The model lacks a "Days-to-Trade" constraint, making it vulnerable to liquidity traps during rebalancing.

### 1.2 'Wrong-Way Risk' in Sector Correlation
The model implements a 50% sector cap (`constraint2`), assuming diversification by limiting exposure to any single sector code.
*   **Wrong-Way Risk:** The optimizer treats sectors as independent buckets. In reality, sectors like 'Financials' and 'Real Estate' often exhibit high correlation during interest rate spikes. A portfolio could be 50% Financials and 50% Real Estate, technically compliant but fundamentally exposed to the same macro-risk factor.
*   **Audit Finding:** The lack of a cross-sector correlation matrix in the objective function leads to "hidden" concentration risk.

### 1.3 'Basis Risk' (Universe vs. Benchmark)
The portfolio is constructed by selecting only 50 stocks from the "Start Universe" and weighting them via a Z-score tilt.
*   **Basis Risk:** There is no constraint on **Tracking Error** relative to the broad market index (the full universe). The optimization process only minimizes distance to a "tilted" target (`Uncapped Wt`), not the benchmark itself.
*   **Audit Finding:** Significant deviation (Basis Risk) from the benchmark is expected. This is a "Feature" of factor indices but a "Risk" if not monitored against a volatility budget.

---

## 2. Quantitative Flaws & Implemented Fixes

### 2.1 Fixed: Critical Mathematical Error in Z-Score Weighting
**Original Flaw:** In `Calculations.const_inputs`, the code multiplied weights by the **raw Z-score** while the variable name implied `(1 + Z_Value)`. This resulted in negative target weights and division by negative numbers in the objective function.
*   **Status:** **FIXED**.
*   **Change:** Replaced with `FCap Wt * (1 + Z_Value)` and added a `.clip(lower=1e-6)` to ensure strictly positive target weights. This aligns the math with financial theory and ensures optimizer stability.

### 2.2 Fixed: Optimization Convergence (Initial Guess)
**Original Flaw:** The initial guess for the optimizer was set to the `Min Wt`, which is at the boundary of the feasible space. This can lead to suboptimal convergence in non-linear problems.
*   **Status:** **FIXED**.
*   **Change:** Updated the initial guess to `Uncapped Wt`. This is a more "natural" starting point and improves the likelihood of finding the global minimum efficiently.

### 2.3 Lingering Risk: Rebalancing 'Zombie' Assets (Buffer Rule)
The "40/60" rule in `rebalance_process_2` allows stocks ranked between 41 and 60 to stay in the index if they were already there.
*   **The Flaw:** If a stock's Z-score is deteriorating but stays within the 41-60 buffer, it remains in the portfolio.
*   **Risk:** This creates a "momentum lag" where the portfolio keeps underperforming assets longer than a pure factor-top-50 approach.

---

## 3. Compliance & Edge Cases

*   **Z-Score Outliers:** Extremely high or low Z-scores (due to data errors) will still disproportionately influence the `Uncapped Wt`. Winsorization of Z-scores is recommended but not yet implemented.
*   **Empty Sectors:** If the rebalance process selects 50 stocks that all belong to only 2 sectors, the 50% cap will be extremely tight, potentially leaving no feasible solution if the `Min Wt` constraints are also high.

**Auditor Note:** *With the fixes implemented, the model is now mathematically sound. However, the qualitative risks regarding liquidity and cross-sector correlation remain and should be monitored via external risk systems.*
