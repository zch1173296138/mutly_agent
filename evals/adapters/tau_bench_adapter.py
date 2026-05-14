from __future__ import annotations

from evals.adapters.base import BaseDatasetAdapter


class TauBenchAdapter(BaseDatasetAdapter):
    dataset_name = "tau-bench"
    dataset_slug = "tau_bench"
    source_url = "https://github.com/sierra-research/tau-bench"
    default_split = "airline_or_retail_needs_review"
    default_tools = ("domain_tool",)

