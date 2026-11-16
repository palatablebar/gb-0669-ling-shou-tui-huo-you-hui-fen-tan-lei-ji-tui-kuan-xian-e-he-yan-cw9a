from copy import deepcopy
from decimal import Decimal
from fastapi.testclient import TestClient
from app.main import app
from app.pipeline import run
from app.modules.m4_calculate.rules import refund_one
from app import store

NOV='2026-11-01T00:00:00+00:00';DEC='2026-12-01T00:00:00+00:00'

def csv_of(lines):
    return '\n'.join(['event_id,order_id,kind,sku,quantity,unit_price,discount,shipping,currency,time,note']+lines)

def two_sales():
    return {
        '000001#1':{'order_id':'000001','sale_id':'000001#1','units':[{'sku':'A','net':100}],'shipping':0,'paid':100,'sold_at':NOV},
        '000001#2':{'order_id':'000001','sale_id':'000001#2','units':[{'sku':'A','net':200}],'shipping':0,'paid':200,'sold_at':DEC},
    }

def ret(event,sku,qty,utc,oid='000001'):
    return {'event_id':event,'order_id':oid,'sku':sku,'qty':qty,'utc':utc,'line':1}

def test_reused_number_keeps_separate_accounts():
    result=run({'sample':'reuse'})
    assert result['model']['order_count']==4
    accounts={a['sale_id']:a for a in result['calculate']['accounts']}
    assert set(accounts)=={'000101','000102','000201#1','000201#2'}
    assert accounts['000201#1']['order_id']==accounts['000201#2']['order_id']=='000201'
    assert accounts['000201#1']['refunded']=='9.01' and accounts['000201#1']['remaining']=='43.98'
    assert accounts['000201#2']['refunded']=='5.00' and accounts['000201#2']['remaining']=='25.00'

def test_late_old_return_never_occupies_new_quota():
    result=run({'sample':'reuse'})
    rows={r['event_id']:r for r in result['calculate']['rows']}
    assert rows['R201N']['sale_id']=='000201#1'
    assert rows['R201D']['sale_id']=='000201#2'

def test_ambiguous_return_quarantined_without_touching_either_sale():
    result=run({'sample':'reuse'})
    assert [r['code'] for r in result['calculate']['quarantine']]==['ORDER_REUSED']
    accounts={a['sale_id']:a for a in result['calculate']['accounts']}
    assert accounts['000201#1']['returned_quantity']==1
    assert accounts['000201#2']['returned_quantity']==1

def test_shared_discount_stays_inside_each_sale():
    result=run({'sample':'reuse'});rows=result['model']['rows']
    nov=[r for r in rows if r['sale_id']=='000201#1'];dec=[r for r in rows if r['sale_id']=='000201#2']
    assert sum(Decimal(r['discount']) for r in nov)==Decimal('5.00')
    assert sum(Decimal(r['discount']) for r in dec)==Decimal('0.00')

def test_reconcile_balanced_with_reused_number():
    result=run({'sample':'reuse'})
    assert result['reconcile']['balanced']
    assert result['reconcile']['paid']=='188.97' and result['reconcile']['refunded']=='32.00'
    assert result['reconcile']['remaining']=='156.97' and result['reconcile']['delta']=='0.00'

def test_ambiguous_return_leaves_state_untouched():
    orders=two_sales();state={}
    result,code=refund_one(orders,state,ret('R1','A',1,'2026-12-15T00:00:00+00:00'))
    assert code=='ORDER_REUSED' and result is None and state=={}

def test_late_return_books_only_old_sale():
    orders=two_sales();state={}
    result,code=refund_one(orders,state,ret('R1','A',1,'2026-11-15T00:00:00+00:00'))
    assert code is None and result['sale_id']=='000001#1' and result['refund']=='1.00'
    assert set(state)=={'000001#1'}

def test_return_before_both_sales_rejected():
    orders=two_sales();state={}
    assert refund_one(orders,state,ret('R1','A',1,'2026-10-15T00:00:00+00:00'))[1]=='BEFORE_SALE'
    assert state=={}

def test_retry_same_return_not_double_deducted():
    orders={'only':{'order_id':'only','units':[{'sku':'A','net':100}],'shipping':0,'paid':100,'sold_at':NOV}};state={}
    row=ret('R1','A',1,'2026-11-15T00:00:00+00:00',oid='only')
    first,code=refund_one(orders,state,row);assert code is None and first['remaining_after']=='0.00'
    before=deepcopy(state)
    assert refund_one(orders,state,row)[1]=='OVER_REFUND'
    assert state==before

def test_failure_recovery_keeps_two_sales_separate():
    result=run({'csv':csv_of([
        'N1,000001,sale,A-01,2,10.00,0,0,CNY,2026-11-01T08:00:00+08:00,旧',
        'D1,000001,sale,B-01,1,20.00,0,0,CNY,2026-12-01T08:00:00+08:00,新',
        'R1,000001,return,A-01,1,0,0,0,CNY,2026-11-10T08:00:00+08:00,退旧',
        'R2,000001,return,B-01,9,0,0,0,CNY,2026-12-10T08:00:00+08:00,超量失败',
        'R3,000001,return,B-01,1,0,0,0,CNY,2026-12-11T08:00:00+08:00,退新',
    ])})
    accounts={a['sale_id']:a for a in result['calculate']['accounts']}
    assert accounts['000001#1']['refunded']=='10.00' and accounts['000001#1']['remaining']=='10.00'
    assert accounts['000001#2']['refunded']=='20.00' and accounts['000001#2']['remaining']=='0.00'
    assert [r['code'] for r in result['calculate']['quarantine']]==['OVER_REFUND']
    assert result['reconcile']['balanced']

def test_late_record_without_old_sale_isolated_not_booked():
    result=run({'csv':csv_of([
        'D1,000001,sale,A-01,1,10.00,0,0,CNY,2026-12-01T08:00:00+08:00,新销售',
        'R1,000001,return,A-01,1,0,0,0,CNY,2026-11-15T10:00:00+08:00,旧单迟到退货',
    ])})
    assert [r['code'] for r in result['calculate']['quarantine']]==['BEFORE_SALE']
    account=result['calculate']['accounts'][0]
    assert account['refunded']=='0.00' and account['remaining']=='10.00'

def test_duplicate_return_row_deducts_once():
    result=run({'csv':csv_of([
        'S1,000001,sale,A-01,2,10.00,0,0,CNY,2026-12-01T08:00:00+08:00,售',
        'R1,000001,return,A-01,1,0,0,0,CNY,2026-12-03T08:00:00+08:00,退',
        'R1,000001,return,A-01,1,0,0,0,CNY,2026-12-03T08:00:00+08:00,退',
    ])})
    assert result['calculate']['refund_total']=='10.00' and result['calculate']['accepted']==1
    assert any(r['code']=='DUPLICATE' for r in result['quarantine']['rows'])

def test_pipeline_retry_is_idempotent():
    client=TestClient(app)
    first=client.post('/api/batches',json={'sample':'reuse'}).json()
    second=client.post('/api/batches',json={'sample':'reuse'}).json()
    assert first['created'] and not second['created']
    assert first['manifest']['run_id']==second['manifest']['run_id']
    assert second['calculate']['refund_total']=='32.00'
    assert len(store.history())==1

def test_review_page_shows_each_refund_sale_and_balances():
    client=TestClient(app)
    rid=client.post('/api/batches',json={'sample':'reuse'}).json()['manifest']['run_id']
    page=client.get('/calculate?run='+rid)
    assert page.status_code==200
    assert '000201#1' in page.text and '000201#2' in page.text and '累计可退' in page.text
    rows={r['event_id']:r for r in client.get('/api/run/'+rid).json()['calculate']['rows']}
    assert rows['R201N']['sale_id']=='000201#1' and rows['R201N']['remaining_after']=='43.98'
    assert rows['R201D']['sale_id']=='000201#2' and rows['R201D']['remaining_after']=='25.00'
    assert rows['R101A']['remaining_after']=='35.00'
    assert all(r['sold_at'] for r in rows.values())
