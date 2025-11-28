
import sys
import os
from datetime import datetime, timedelta

# Add app to path
sys.path.append(os.getcwd())

from sqlmodel import select, Session
from app.db.session import engine, init_db
from app.models import CaptureLog, NeoCandidate, NeoEphemeris, CandidateAssociation, AstrometricSolution, Measurement, NeoObservability

def verify_association_logic():
    print("Initializing DB...")
    init_db()
    
    with Session(engine) as session:
        # Cleanup previous test data
        session.exec(CandidateAssociation.__table__.delete())
        session.exec(NeoEphemeris.__table__.delete())
        session.exec(AstrometricSolution.__table__.delete())
        session.exec(Measurement.__table__.delete())
        session.exec(CaptureLog.__table__.delete())
        session.exec(NeoObservability.__table__.delete())
        session.exec(NeoCandidate.__table__.delete())
        session.commit()

        print("Creating test data...")
        # 1. Create Candidate
        candidate = NeoCandidate(trksub="TEST001", created_at=datetime.utcnow(), updated_at=datetime.utcnow())
        session.add(candidate)
        session.commit()
        session.refresh(candidate)
        
        # 2. Create Capture
        now = datetime.utcnow()
        capture = CaptureLog(
            kind="light",
            target="TEST001",
            path="/tmp/test_capture.fits",
            started_at=now
        )
        session.add(capture)
        session.commit()
        session.refresh(capture)
        
        # 3. Create Ephemeris
        ephemeris = NeoEphemeris(
            candidate_id=candidate.id,
            trksub="TEST001",
            epoch=now,
            ra_deg=10.5,
            dec_deg=20.5,
            created_at=now
        )
        session.add(ephemeris)
        session.commit()
        
        print(f"Created Candidate ID: {candidate.id}, Capture ID: {capture.id}")

        # 4. Simulate Manual Association (Direct DB insertion as async function call is hard to mock fully without Request)
        print("Simulating manual association...")
        assoc = CandidateAssociation(
            capture_id=capture.id,
            ra_deg=10.6,
            dec_deg=20.6
        )
        session.add(assoc)
        session.commit()
        
        # Verify persistence
        saved_assoc = session.exec(select(CandidateAssociation).where(CandidateAssociation.capture_id == capture.id)).first()
        if saved_assoc:
            print(f"SUCCESS: Association persisted. RA={saved_assoc.ra_deg}, Dec={saved_assoc.dec_deg}")
        else:
            print("FAILURE: Association not found in DB.")
            return

        # 5. Verify Query Logic (Simulate association_partial logic)
        print("Verifying query logic...")
        
        # Re-implementing the core query logic from dashboard.py to verify it works with these models
        # (We can't easily call the route function because it returns a TemplateResponse and needs a Request)
        
        # Load captures
        captures = session.exec(
            select(CaptureLog).where(CaptureLog.target == "TEST001")
        ).all()
        
        # Load associations
        capture_ids = [c.id for c in captures]
        associations = session.exec(
            select(CandidateAssociation).where(CandidateAssociation.capture_id.in_(capture_ids))
        ).all()
        
        # Load predictions
        eph_rows = session.exec(
            select(NeoEphemeris)
            .where(
                NeoEphemeris.candidate_id == candidate.id
            )
        ).all()
        
        if len(captures) == 1:
            print("SUCCESS: Capture found.")
        else:
            print(f"FAILURE: Expected 1 capture, found {len(captures)}")
            
        if len(associations) == 1:
            print("SUCCESS: Association found.")
        else:
            print(f"FAILURE: Expected 1 association, found {len(associations)}")
            
        if len(eph_rows) == 1:
            print("SUCCESS: Ephemeris found.")
        else:
            print(f"FAILURE: Expected 1 ephemeris, found {len(eph_rows)}")

if __name__ == "__main__":
    verify_association_logic()
