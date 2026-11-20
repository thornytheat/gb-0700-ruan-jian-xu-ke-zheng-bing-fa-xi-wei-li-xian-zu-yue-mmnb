from app.timeops import instant
from app.report import report,quarantine
from app.errors import BusinessError
from app.numbers import number
ALIASES={"CAD-Pro":"CAD","cad":"CAD","CAD":"CAD","Render":"RENDER","RENDER":"RENDER"}
POINT_KINDS={"heartbeat","release","request","grant","abandon"}
ENTITY_KINDS={"online","offline","capacity"}
def product(text):
    if text not in ALIASES:raise BusinessError("PRODUCT","产品未登记")
    return ALIASES[text]

def seats(text):
    value=number(text)
    if value<=0 or value!=value.to_integral_value():raise BusinessError("SEATS","席位须为正整数")
    return int(value)

def apply(ctx):
    out=[]
    for r in ctx["raw"]:
        try:
            x=dict(r);x["canonical"]=product(r["product"]);x["a"]=instant(r["start"]);x["b"]=instant(r["end"]) if r["end"] else x["a"];x["rev"]=int(r["version"]);x["quantity"]=seats(r["seats"])
            if x["rev"]<1:raise BusinessError("VERSION","版本须为正")
            if r["kind"] not in POINT_KINDS|ENTITY_KINDS:raise BusinessError("KIND","事件类型无效")
            if r["kind"] in ENTITY_KINDS and x["b"]<=x["a"]:raise BusinessError("INTERVAL","租约区间无效")
            if r["kind"] in POINT_KINDS and r["end"]:raise BusinessError("POINT_EVENT","点事件不得填写结束时间")
            out.append(x)
        except (BusinessError,ValueError) as e:quarantine(ctx,r,getattr(e,"code","VERSION"))
    ctx["normalized"]=out;return report(out,产品=len({r["canonical"] for r in out}),有效事件=len(out))
