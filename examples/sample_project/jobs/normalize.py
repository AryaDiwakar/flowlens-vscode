from pathlib import Path

import pandas as pd

from .helpers import clean_email

DATA = Path(__file__).parents[1] / "data" / "customers.csv"
customers = pd.read_csv(str(DATA))  # Dynamic Path expression is intentionally not resolved statically.
customers["email"] = customers["email"].map(clean_email)
