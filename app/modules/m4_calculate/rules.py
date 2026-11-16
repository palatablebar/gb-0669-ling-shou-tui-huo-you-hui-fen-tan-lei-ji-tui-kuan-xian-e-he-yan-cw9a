from app.money import amount

def refund_one(orders,state,row):
    oid=row['order_id'];sku=row['sku'];qty=row['qty']
    owned=[(sid,o) for sid,o in orders.items() if o.get('order_id',sid)==oid]
    if not owned:return None,'ORDER_MISSING'
    eligible=[(sid,o) for sid,o in owned if row['utc']>=o['sold_at']]
    if not eligible:return None,'BEFORE_SALE'
    holders=[(sid,o) for sid,o in eligible if any(u['sku']==sku for u in o['units'])]
    if not holders:return None,'SKU_MISSING'
    if len(holders)>1:return None,'ORDER_REUSED'
    sid,order=holders[0]
    returned=state.setdefault(sid,{'indices':[],'refunded':0,'shipping_refunded':False})
    remaining=[(i,u) for i,u in enumerate(order['units']) if u['sku']==sku and i not in returned['indices']]
    if len(remaining)<qty:return None,'OVER_REFUND'
    chosen=remaining[:qty];new_indices=returned['indices']+[i for i,u in chosen]
    shipping=order['shipping'] if len(new_indices)==len(order['units']) and not returned['shipping_refunded'] else 0
    value=sum(u['net'] for i,u in chosen)+shipping
    if returned['refunded']+value>order['paid']:return None,'OVER_REFUND'
    returned['indices']=new_indices;returned['refunded']+=value;returned['shipping_refunded']=returned['shipping_refunded'] or shipping>0
    return {'event_id':row['event_id'],'order_id':oid,'sale_id':sid,'sold_at':order['sold_at'],'sku':sku,'quantity':qty,'refund':amount(value),'shipping':amount(shipping),'refund_cents':value,'shipping_cents':shipping,'remaining_after':amount(order['paid']-returned['refunded']),'line':row['line'],'status':'通过'},None
