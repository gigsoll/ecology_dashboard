from app.db.database import get_session
from app.db.parameter_norms import apply_parameter_norms
from app.db.schemas import DimParameter


def sync_parameter_norms() -> int:
    session = get_session()
    try:
        rows = session.query(DimParameter).filter(DimParameter.is_current.is_(True)).all()
        changed = 0
        for row in rows:
            if apply_parameter_norms(row):
                changed += 1
        session.commit()
        return changed
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    total = sync_parameter_norms()
    print(f"Updated norms for {total} current parameters.")
