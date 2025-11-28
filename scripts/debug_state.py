from app.db.session import get_session
from app.models import NeoObservability, NeoCandidate
from sqlmodel import select
from datetime import datetime

with get_session() as session:
    now = datetime.utcnow()
    print(f"Current UTC Time: {now}")
    
    # Check total candidates
    count = session.exec(select(NeoCandidate)).all()
    print(f"Total Candidates: {len(count)}")
    
    # Check observability
    obs = session.exec(select(NeoObservability)).all()
    print(f"Total Observability Records: {len(obs)}")
    
    # Check observable now
    stmt = select(NeoObservability).where(NeoObservability.is_observable == True)
    stmt = stmt.where(NeoObservability.window_start <= now)
    stmt = stmt.where(NeoObservability.window_end > now)
    valid = session.exec(stmt).all()
    print(f"Observable NOW: {len(valid)}")
    
    if not valid and obs:
        print("Sample blocked reasons:")
        for o in obs[:3]:
            print(f"  {o.trksub}: is_observable={o.is_observable}, factors={o.limiting_factors}")
