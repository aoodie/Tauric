"""Check connected market catalogs and bounded browsing prices in the local preview."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = """
import json
from sqlalchemy import func,select
from sqlalchemy.orm import Session
from tauric import intelligence,intelligence_catalog
from tauric.database import engine
from tauric.intelligence_contracts import IntelligenceRefresh
from tauric.models import Owner,Instrument,AnalysisJob
with Session(engine()) as db:
    owner=db.scalar(select(Owner))
    before={name:db.scalar(select(func.count()).select_from(model)) for name,model in [('research_jobs',AnalysisJob),('registered_instruments',Instrument)]}
    catalogs=intelligence_catalog.synchronize(db,owner)
    keys=['catalog:alpaca:NVDA','catalog:oanda:AUD_CAD','catalog:binance:ADAUSDT']
    result=intelligence.refresh(db,owner,IntelligenceRefresh(instrument_ids=keys,sources=False))
    observations=[]
    for family,query in [('stocks','NVDA'),('fx','AUD/CAD'),('crypto','ADA/USDT')]:
        view=intelligence.dashboard(db,owner,family,query=query)
        move=next(m for m in view.moves if m.instrument.symbol==query)
        assert move.price and move.price_receipt_id and move.changes['1m'], 'Missing verified browsing prices: '+query
        assert not move.instrument.registered
        observations.append({'market':family,'symbol':query,'registered':move.instrument.registered,'state':move.state,'price':move.price,'changes':move.changes,'observed_at':move.observed_at.isoformat(),'daily_bars':len(move.daily_bars),'price_receipt_id':move.price_receipt_id})
    after={name:db.scalar(select(func.count()).select_from(model)) for name,model in [('research_jobs',AnalysisJob),('registered_instruments',Instrument)]}
    assert before==after,'Browsing modified research enrollment or started a job.'
    print(json.dumps({'catalogs':[c.model_dump(mode='json') for c in catalogs],'observations':observations,'before':before,'after':after,'refresh_results':result.results}))
"""

result = subprocess.check_output(
    [
        "docker",
        "compose",
        "--env-file",
        str(ROOT / ".env"),
        "-f",
        str(ROOT / "deploy/compose.dev.yml"),
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        CODE,
    ],
    text=True,
)
report = json.loads(result)
(ROOT / "artifacts/intelligence-catalog-live.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print(json.dumps(report))
