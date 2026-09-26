from fastapi import APIRouter, Depends, HTTPException, Request, status

from app import facts
from app.auth import require_api_key
from app.features.proof.verdicts import AdvisoryProof, fold_proofs

router = APIRouter(prefix="/features/proof", tags=["proof"], dependencies=[Depends(require_api_key)])


@router.get("/{run_id}", response_model=list[AdvisoryProof])
async def get_proof(run_id: str, request: Request) -> list[AdvisoryProof]:
    if await request.app.state.ledger.get_run(run_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found")
    await facts.flush()
    return fold_proofs(await facts.get_facts(run_id, kind="proof"))
