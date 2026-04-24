from pydantic import BaseModel


class LinkGenerationRequest(BaseModel):
    mode: str = "test"       # "test" | "full" | "custom"
    item_limit: int = 5
    start_row: int = 1
    end_row: int = 100


class ScrapingRequest(BaseModel):
    mode: str = "test"       # "test" | "full" | "missing" | "custom"
    item_limit: int = 3
    start_row: int = 1
    end_row: int = 100
    num_workers: int = 0     # 0 = auto-detect based on CPU
    sort_order: str = "low_to_high"  # "low_to_high" | "high_to_low"
    stop_after: int = 0      # 0 = no limit; N = stop once N rows are completed
    headless: bool = True    # True = no browser window; False = visible browser


class LinkExtractionRequest(BaseModel):
    sort_order: str = "low_to_high"  # "low_to_high" | "high_to_low"
    num_workers: int = 0              # 0 = auto-detect based on CPU count
    stop_after: int = 0               # 0 = no limit; N = stop once N links are completed
    headless: bool = True             # True = no browser window; False = visible browser
