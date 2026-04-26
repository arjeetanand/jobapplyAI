from app.api.shared import *

router = APIRouter()


@router.post("/answers")
def create_answer(payload: ApplicationAnswerIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else _default_user(db)
    answer = ApplicationAnswer(user_id=user.id, **payload.model_dump(exclude={"user_id"}))
    db.add(answer)
    db.commit()
    db.refresh(answer)
    return {"answer_id": answer.id, "message": "Application answer saved.", "approved": answer.approved}


@router.post("/answers/bulk")
def bulk_upsert_answers(payload: BulkApplicationAnswersIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else _default_user(db)
    preferences = _preferences_for(db, user)
    saved: list[ApplicationAnswer] = []

    for item in payload.answers:
        key = _canonical_question_key(item.question_text, item.question_key)
        answer_text = item.answer_text.strip()
        if not answer_text:
            continue

        existing = db.scalar(
            select(ApplicationAnswer).where(
                ApplicationAnswer.user_id == user.id,
                ApplicationAnswer.question_key == key,
            )
        )
        if existing:
            existing.question_text = item.question_text
            existing.answer_text = answer_text
            existing.source = item.source
            existing.sensitive = item.sensitive
            existing.approved = item.approved
            answer = existing
        else:
            answer = ApplicationAnswer(
                user_id=user.id,
                question_key=key,
                question_text=item.question_text,
                answer_text=answer_text,
                source=item.source,
                sensitive=item.sensitive,
                approved=item.approved,
            )
            db.add(answer)

        _sync_profile_answer(db, user, preferences, key, answer_text)
        saved.append(answer)

    db.add(
        AgentRun(
            agent_name="Application Answer Bank",
            input_summary=f"user_id={user.id}",
            output_summary=f"Bulk saved {len(saved)} resume intake answers",
        )
    )
    db.commit()
    for answer in saved:
        db.refresh(answer)

    answers = _answers_for_user(db, user)
    return {
        "user_id": user.id,
        "saved_count": len(saved),
        "answers": [_answer_payload(answer) for answer in saved],
        "missing_questions": _missing_profile_questions(user, preferences, answers),
        "message": f"Saved {len(saved)} answer(s) to the knowledge base.",
    }


@router.get("/answers")
def list_answers(user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    answers = db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user.id)).all()
    return {
        "answers": [
            {
                "id": answer.id,
                "question_key": answer.question_key,
                "question_text": answer.question_text,
                "answer_text": answer.answer_text,
                "source": answer.source,
                "sensitive": answer.sensitive,
                "approved": answer.approved,
            }
            for answer in answers
        ]
    }


@router.get("/answers/suggest")
def suggest_answers(job_id: int, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    job = _job_or_404(db, job_id)
    answers = db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user.id)).all()
    packet = ApplicationPacketAgent().prepare(user, job, None, None, answers)
    return {"suggested_answers": packet.packet["answers"], "missing_items": packet.missing_items}


@router.post("/answers/{answer_id}/approve")
def approve_answer(answer_id: int, db: Session = Depends(get_db)) -> dict:
    answer = db.get(ApplicationAnswer, answer_id)
    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found.")
    answer.approved = True
    db.commit()
    return {"answer_id": answer.id, "approved": True}


@router.post("/claim-ledger")
def create_claim(payload: ClaimLedgerIn, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, payload.user_id) if payload.user_id else _default_user(db)
    claim = ClaimLedgerItem(user_id=user.id, **payload.model_dump(exclude={"user_id"}))
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return {"claim_id": claim.id, "message": "Claim ledger item saved.", "approved": claim.approved}


@router.get("/claim-ledger")
def list_claims(user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    claims = db.scalars(select(ClaimLedgerItem).where(ClaimLedgerItem.user_id == user.id)).all()
    return {
        "claims": [
            {
                "id": claim.id,
                "claim_type": claim.claim_type,
                "claim_text": claim.claim_text,
                "source": claim.source,
                "approved": claim.approved,
            }
            for claim in claims
        ]
    }


@router.post("/jobs/{job_id}/prepare-application-packet")
def prepare_application_packet(job_id: int, user_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id) if user_id else _default_user(db)
    job = _job_or_404(db, job_id)
    resume = db.scalar(
        select(ResumeVersion)
        .where(ResumeVersion.user_id == user.id, ResumeVersion.job_id == job.id)
        .order_by(ResumeVersion.created_at.desc())
    )
    application = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
    email = None
    if application:
        email = db.scalar(select(Email).where(Email.application_id == application.id).order_by(Email.created_at.desc()))
    answers = db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.user_id == user.id)).all()
    prepared = ApplicationPacketAgent().prepare(user, job, resume, email, answers)
    row = ApplicationPacket(
        user_id=user.id,
        job_id=job.id,
        resume_version_id=resume.id if resume else None,
        email_id=email.id if email else None,
        packet_json=prepared.packet,
        status="Prepared for review",
    )
    db.add(row)
    db.add(
        AgentRun(
            agent_name="Application Packet Agent",
            input_summary=f"job_id={job.id}",
            output_summary=f"Prepared packet with {len(prepared.missing_items)} missing items",
        )
    )
    db.commit()
    db.refresh(row)
    return {"packet_id": row.id, "packet": prepared.packet, "missing_items": prepared.missing_items}
