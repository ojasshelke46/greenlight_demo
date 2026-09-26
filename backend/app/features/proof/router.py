from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app import facts, orchestrator
from app.auth import require_api_key
from app.features.proof.verdicts import AdvisoryProof, fold_proofs

router = APIRouter(prefix="/features/proof", tags=["proof"], dependencies=[Depends(require_api_key)])


class ProverStatus(BaseModel):
    run_id: str
    advisory: str | None
    run_status: str
    # still_exploitable blocks the merge; running, error and inconclusive are shown but never block.
    state: Literal["running", "proven", "still_exploitable", "error", "inconclusive"]
    result: dict[str, Any] | None


class Verification(BaseModel):
    status: Literal["none", "running", "proven", "still_exploitable", "error", "inconclusive"]
    provers: list[ProverStatus]


class ProofResponse(BaseModel):
    advisories: list[AdvisoryProof]
    verification: Verification


@router.get("/{run_id}", response_model=ProofResponse)
async def get_proof(run_id: str, request: Request) -> ProofResponse:
    if await request.app.state.ledger.get_run(run_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found")
    await facts.flush()
    running = orchestrator.current()
    verification = await running.verification(run_id) if running is not None else {"status": "none", "provers": []}
    return ProofResponse(
        advisories=fold_proofs(await facts.get_facts(run_id, kind="proof")),
        verification=Verification(**verification),
    )
