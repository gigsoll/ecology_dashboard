#!/bin/env python
from sqlalchemy.orm import Session
import pandas as pd

from app.services.data_prep import prepare_data
from app.db.fill_db import run_pipeline
from app.db.database import get_session


def main() -> None:
    df: pd.DataFrame = prepare_data()
    session: Session = get_session()
    run_pipeline(
        df=df,
        session=session,
    )


if __name__ == "__main__":
    main()
