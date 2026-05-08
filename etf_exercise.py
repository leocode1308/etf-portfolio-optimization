# -*- coding: utf-8 -*-
"""
Portfolio Optimization and Rebalancing Module.
This module provides classes and functions for index creation, 
rebalancing, and factor-tilted weight optimization.
"""

import os
import datetime as dt
import warnings
import pandas as pd
import numpy as np
from scipy.optimize import minimize
from openpyxl import load_workbook

# Silence warnings for cleaner output in production
warnings.filterwarnings("ignore")


class PortfolioConfig:
    """
    Configuration container for portfolio data source and metadata.
    """

    def __init__(self, inputs_path: str, inputs_file: str, inputs_sheet_name: str):
        """
        Initializes the portfolio configuration.

        Args:
            inputs_path (str): Directory path containing the input files.
            inputs_file (str): Filename of the Excel workbook.
            inputs_sheet_name (str): Sheet name containing the universe data.
        """
        self.inputs_path = inputs_path
        self.inputs_file = inputs_file
        self.inputs_sheet_name = inputs_sheet_name


class PortfolioOptimizer:
    """
    Handles rebalancing logic and weight optimization for factor-tilted portfolios.
    """

    def __init__(self, universe_data: pd.DataFrame, method: str = 'SLSQP', 
                 min_weight: float = 0.5 / 1000, portfolio_size: int = 50):
        """
        Initializes the optimizer with universe data and constraints.

        Args:
            universe_data (pd.DataFrame): DataFrame containing asset universe.
            method (str): Optimization algorithm (default: 'SLSQP').
            min_weight (float): Minimum allowed weight per asset.
            portfolio_size (int): Target number of assets in the portfolio.
        """
        self.universe_data = universe_data
        self.method = method
        self.min_weight = min_weight
        self.portfolio_size = portfolio_size
        self.constraints = []
        self.bounds = []
        self.sector_weights = None

    def create_initial_portfolio(self, universe_data: pd.DataFrame) -> pd.DataFrame:
        """
        Creates the initial portfolio by selecting the top assets based on Z-Value.

        Args:
            universe_data (pd.DataFrame): Filtered universe for a specific date.

        Returns:
            pd.DataFrame: Top assets selected for the initial portfolio.
        """
        # Sort by the last column (Z_Value) descending
        universe_data.sort_values(by=universe_data.columns[-1], ascending=False, inplace=True)
        return universe_data.iloc[0:self.portfolio_size, :].copy()

    def prepare_optimization_inputs(self, portfolio: pd.DataFrame) -> pd.DataFrame:
        """
        Calculates target weights and bounds for the optimization process.

        Args:
            portfolio (pd.DataFrame): Selected portfolio constituents.

        Returns:
            pd.DataFrame: Portfolio constituents with calculated optimization parameters.
        """
        portfolio_inputs = portfolio.copy()
        
        # Auditor Fix: Ensuring positive target weights using (1 + Z_Value) transformation
        portfolio_inputs['Target_Factor_Score'] = portfolio_inputs['FCap Wt'] * (1 + portfolio_inputs['Z_Value'])
        portfolio_inputs['Target_Factor_Score'] = portfolio_inputs['Target_Factor_Score'].clip(lower=1e-6)
        
        # Calculate Uncapped Target Distribution
        total_score = portfolio_inputs['Target_Factor_Score'].sum()
        portfolio_inputs['Target_Distribution'] = portfolio_inputs['Target_Factor_Score'] / total_score
        
        # Asset Concentration Limits
        portfolio_inputs['Max_Wt_Multiplier'] = 20 * portfolio_inputs['FCap Wt']
        portfolio_inputs['Max_Wt_Hard_Cap'] = 0.05
        portfolio_inputs['Max_Weight'] = portfolio_inputs[['Max_Wt_Multiplier', 'Max_Wt_Hard_Cap']].min(axis=1)
        portfolio_inputs['Min_Weight'] = self.min_weight
        
        portfolio_inputs.reset_index(inplace=True, drop=True)
        portfolio_inputs.drop(['Max_Wt_Multiplier', 'Max_Wt_Hard_Cap'], axis=1, inplace=True)
        
        # Sector Exposure Summary
        self.sector_weights = portfolio_inputs.groupby('Sector Code').agg({
            'Company Name': 'count',
            'Target_Distribution': 'sum'
        }).reset_index()
        self.sector_weights.columns = ['Sector Code', 'Stock_Count', 'Target_Sector_Exposure']
        
        return portfolio_inputs

    def objective_function(self, weights: np.array, target_distribution: np.array) -> float:
        """
        Chi-squared distance objective function for tracking error minimization.

        Args:
            weights (np.array): Optimized weights being solved for.
            target_distribution (np.array): Desired target distribution based on factor tilt.

        Returns:
            float: Objective value to be minimized.
        """
        # Formula: sum((weights - target)^2 / target)
        objective_value = np.sum(((weights - target_distribution) ** 2) / target_distribution)
        return objective_value

    def budget_constraint(self, weights: np.array) -> float:
        """
        Ensures total portfolio weight equals 100%.

        Args:
            weights (np.array): Portfolio asset weights.

        Returns:
            float: Deviation from 1.0 (should be 0).
        """
        return np.sum(weights) - 1.0

    def sector_cap_constraint(self, weights: np.array, asset_indices: list) -> float:
        """
        Ensures a specific sector exposure does not exceed 50%.

        Args:
            weights (np.array): Portfolio asset weights.
            asset_indices (list): Indices of assets belonging to the sector.

        Returns:
            float: Remaining headroom below 50%.
        """
        sector_sum = np.sum(weights[asset_indices])
        return 0.5 - sector_sum

    def build_constraints(self, portfolio_inputs: pd.DataFrame) -> list:
        """
        Generates equality and inequality constraints for the optimizer.

        Args:
            portfolio_inputs (pd.DataFrame): Data containing sector mappings and indices.

        Returns:
            list: Dictionary objects defining constraints for scipy.minimize.
        """
        self.constraints = [{'type': 'eq', 'fun': self.budget_constraint}]
        
        # Add 50% cap for each unique sector
        for sector_code in portfolio_inputs['Sector Code'].unique():
            sector_indices = portfolio_inputs.index[portfolio_inputs['Sector Code'] == sector_code].tolist()
            self.constraints.append({
                'type': 'ineq', 
                'fun': self.sector_cap_constraint, 
                'args': (sector_indices,)
            })
        return self.constraints

    def build_bounds(self, min_weights: np.array, max_weights: np.array) -> list:
        """
        Defines lower and upper bounds for each asset's weight.

        Args:
            min_weights (np.array): Array of lower bounds.
            max_weights (np.array): Array of upper bounds.

        Returns:
            list: Tuples of (min, max) for each asset.
        """
        self.bounds = list(zip(min_weights, max_weights))
        return self.bounds

    def optimize_portfolio_weights(self, initial_guess: np.array, 
                                   target_distribution: np.array) -> tuple:
        """
        Executes the numerical optimization to find optimal weights.

        Args:
            initial_guess (np.array): Starting point for the optimizer.
            target_distribution (np.array): Target distribution to track.

        Returns:
            scipy.optimize.OptimizeResult: Optimization results including weights and status.
        """
        return minimize(
            self.objective_function, 
            initial_guess, 
            args=(target_distribution,),
            method=self.method, 
            bounds=self.bounds,
            constraints=self.constraints
        )

    def apply_rebalance_rules(self, current_portfolio: pd.DataFrame, 
                              universe_at_date: pd.DataFrame) -> pd.DataFrame:
        """
        Main rebalancing workflow implementing the 40/60 buffer rules.

        Args:
            current_portfolio (pd.DataFrame): Portfolio from the previous period.
            universe_at_date (pd.DataFrame): Asset universe available at current date.

        Returns:
            pd.DataFrame: New portfolio constituents after rebalancing.
        """
        universe_at_date.sort_values(by=universe_at_date.columns[-1], ascending=False, inplace=True)
        
        # Step 1: Automatic inclusion for top 40 assets
        step1_constituents = universe_at_date.iloc[0:40, :].copy()
        
        if len(step1_constituents) == self.portfolio_size:
            return step1_constituents

        # Step 2: Buffer rule - keep existing constituents if ranked 41-60
        buffer_zone = universe_at_date.iloc[40:60, :].copy()
        existing_names = current_portfolio['Company Name'].tolist()
        step1_names = step1_constituents['Company Name'].tolist()
        
        candidates_in_buffer = [name for name in existing_names if name not in step1_names]
        
        step2_constituents = buffer_zone[buffer_zone['Company Name'].isin(candidates_in_buffer)]
        combined_portfolio = pd.concat([step1_constituents, step2_constituents])
        
        if len(combined_portfolio) == self.portfolio_size:
            return combined_portfolio

        # Step 3: Fill remaining slots with next best candidates
        remaining_slots = self.portfolio_size - len(combined_portfolio)
        used_names = combined_portfolio['Company Name'].tolist()
        
        final_candidates = universe_at_date[~universe_at_date['Company Name'].isin(used_names)]
        final_candidates = final_candidates.sort_values(by='Z_Value', ascending=False).iloc[0:remaining_slots, :]
        
        new_portfolio = pd.concat([combined_portfolio, final_candidates])
        new_portfolio.reset_index(inplace=True, drop=True)
        return new_portfolio

    def process_rebalance_period(self, current_portfolio: pd.DataFrame, 
                                 ref_date: dt.date) -> tuple:
        """
        Coordinates the rebalance and optimization for a specific date.

        Args:
            current_portfolio (pd.DataFrame): Existing portfolio.
            ref_date (dt.date): Current rebalance date.

        Returns:
            tuple: (Optimized DataFrame, Objective Value)
        """
        universe_at_date = self.universe_data[self.universe_data['Ref Date'] == ref_date].copy()
        
        # Rebalance Constituents
        new_portfolio = self.apply_rebalance_rules(current_portfolio, universe_at_date)
        
        # Prepare and Execute Optimization
        opt_data = self.prepare_optimization_inputs(new_portfolio)
        self.build_constraints(opt_data)
        self.build_bounds(opt_data['Min_Weight'].values, opt_data['Max_Weight'].values)
        
        initial_guess = opt_data['Target_Distribution'].values
        result = self.optimize_portfolio_weights(initial_guess, opt_data['Target_Distribution'].values)
        
        if not result.success:
            print(f"Warning: Optimization for {ref_date} failed: {result.message}")
        
        # Assign optimized weights
        new_portfolio['Capped Wt'] = result.x
        new_portfolio.sort_values(by='Z_Value', ascending=False, inplace=True)
        
        return new_portfolio, result.fun


def main():
    """
    Main entry point for the Portfolio Rebalancing execution.
    """
    # Configuration
    config = PortfolioConfig(
        inputs_path='.', 
        inputs_file='PythonAssessment.xlsx', 
        inputs_sheet_name='Start Universe'
    )
    
    # Data Loading
    #data_path = os.path.join(config.inputs_path, config.inputs_file)
    data_path = r'/home/matei/proyectos/etf_leo/PythonAssessment.xlsx'
    universe_df = pd.read_excel(data_path, sheet_name=config.inputs_sheet_name, engine='openpyxl')
    #universe_df = pd.read_excel(data_path, sheet_name=config.inputs_sheet_name)
    universe_df['Ref Date'] = universe_df['Ref Date'].dt.date
    
    # Initialize Optimizer
    optimizer = PortfolioOptimizer(universe_df)
    
    # Initial Portfolio Construction
    min_date = min(universe_df['Ref Date'].unique())
    initial_universe = universe_df[universe_df['Ref Date'] == min_date]
    current_portfolio = optimizer.create_initial_portfolio(initial_universe)
    
    results_history = []
    
    # Iterate through rebalance dates
    rebalance_dates = sorted(universe_df['Ref Date'].unique())[1:]
    
    for date in rebalance_dates:
        print(f"Processing Rebalance for: {date}")
        optimized_portfolio, obj_val = optimizer.process_rebalance_period(current_portfolio, date)
        print(f"Optimization Objective Value: {obj_val:.6f}")
        
        results_history.append(optimized_portfolio)
        current_portfolio = optimized_portfolio

# --- PROFESSIONAL DATA PERSISTENCE BLOCK ---
    # 1. Define a different output path to protect the original source file
    output_path = "Optimized_Portfolio_Results.xlsx"
    
    # 2. Define the requested sheet names
    sheet_names = ['Dic_2017_test_1_2', 'Jun_2018_test_1_2']
    
    print(f"Saving results to {output_path}...")
    
    # 3. Use mode='w' to create a fresh file and avoid corruption
    with pd.ExcelWriter(output_path, engine='openpyxl', mode='w') as writer:
        for i, portfolio_df in enumerate(results_history):
            if i < len(sheet_names):
                target_sheet = sheet_names[i]
                portfolio_df.to_excel(
                    writer, 
                    sheet_name=target_sheet, 
                    float_format="%.6f", 
                    index=False
                )
                print(f"✅ Sheet '{target_sheet}' generated successfully.")

    print(f"\n🚀 Process completed successfully.")
    print(f"Input file (Intact): {data_path}")
    print(f"Output file (Generated): {output_path}")


if __name__ == '__main__':
    main()