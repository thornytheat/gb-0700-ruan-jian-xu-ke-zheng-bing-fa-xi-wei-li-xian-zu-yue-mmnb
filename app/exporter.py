import csv,io,json

def evidence_csv(run):
    out=io.StringIO();writer=csv.writer(out)
    writer.writerow(["run_id","sha256","rule_version","kind","line","reason","evidence"])
    writer.writerow([run["id"],run["digest"],run["rule_version"],"manifest","","",json.dumps({"balance":run["result"]["balance"],"decision":run["decision"],"revision":run["revision"]},ensure_ascii=False)])
    for row in run["result"].get("final",[]):
        writer.writerow([run["id"],run["digest"],run["rule_version"],"result",row.get("line",""),row.get("status",""),json.dumps(row,ensure_ascii=False)])
    for row in run["result"].get("wait_tickets",[]):
        writer.writerow([run["id"],run["digest"],run["rule_version"],"wait_ticket",row.get("request_lines",""),row.get("status",""),json.dumps(row,ensure_ascii=False)])
    for row in run["result"].get("shortage",[]):
        writer.writerow([run["id"],run["digest"],run["rule_version"],"shortage",row.get("a",""),row.get("product",""),json.dumps(row,ensure_ascii=False)])
    for row in run["result"].get("quarantine",[]):
        writer.writerow([run["id"],run["digest"],run["rule_version"],"quarantine",row.get("line",""),row["reason"],json.dumps(row,ensure_ascii=False)])
    return "\ufeff"+out.getvalue()
