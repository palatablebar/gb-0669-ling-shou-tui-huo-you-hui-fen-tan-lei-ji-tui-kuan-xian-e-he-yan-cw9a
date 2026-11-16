from collections import defaultdict
from .rules import deduplicate,allocate_units
from app.reasons import issue
from app.money import amount

def sale_ids(groups):
    ids={}
    for order_id in sorted({key[0] for key in groups}):
        slots=sorted(key[1] for key in groups if key[0]==order_id)
        for seq,utc in enumerate(slots,1):ids[(order_id,utc)]=order_id if len(slots)==1 else f'{order_id}#{seq}'
    return ids

def execute(rows):
    rows,bad=deduplicate(rows);sales=defaultdict(list);returns=[]
    for row in rows:
        if row['kind']=='sale':sales[(row['order_id'],row['utc'])].append(row)
        else:returns.append(row)
    ids=sale_ids(sales);orders={};evidence=[];accepted=[]
    for key,lines in sorted(sales.items()):
        order_id=key[0];sale_id=ids[key];code=None
        if len({(r['discount_cents'],r['shipping_cents']) for r in lines})>1:code='ORDER_PROMO'
        elif len({r['sku'] for r in lines})<len(lines):code='SKU_CONFLICT'
        elif sum(r['qty'] for r in lines)>10000:code='TOO_MANY_UNITS'
        if code:bad.extend(issue(r,code) for r in lines);continue
        order,code=allocate_units(lines)
        if code:bad.extend(issue(r,code) for r in lines);continue
        order['sale_id']=sale_id;order['order_id']=order_id
        orders[sale_id]=order;accepted.extend(lines)
        for line in lines:
            selected=[u for u in order['units'] if u['sku']==line['sku']]
            evidence.append({'sale_id':sale_id,'order_id':order_id,'sku':line['sku'],'quantity':line['qty'],'gross':amount(sum(u['price'] for u in selected)),'discount':amount(sum(u['discount'] for u in selected)),'net':amount(sum(u['net'] for u in selected)),'line':line['line']})
    return {'orders':orders,'rows':evidence,'sale_events':accepted,'returns':returns,'quarantine':bad,'order_count':len(orders),'paid':amount(sum(o['paid'] for o in orders.values()))}
