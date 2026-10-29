"""Synthetic SQL cardinality only; sampled native accounts are enrolled first."""
from __future__ import annotations

from sqlalchemy import text


def seed(engine, plan, *, enrolled: int = 8) -> dict:
    if enrolled != 8:
        raise ValueError("Eight genuinely enrolled actors are required")
    divisor = 5 if plan.buyers_percent == 20 else 4
    with engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL statement_timeout=120000")
        assert connection.execute(text("SELECT count(*) FROM candidate_password_accounts WHERE state='ACTIVE'")).scalar_one() == enrolled
        connection.exec_driver_sql("""INSERT INTO users(id,email,password_hash,credential_epoch,tier,ai_credits,job_service_credits)
            SELECT i,'capacity-'||i||'@example.test','',0,'free',0,0 FROM generate_series(9,10000) i""")
        connection.exec_driver_sql("""INSERT INTO resumes(id,user_id,created_at,original_filename,raw_text,source_document,source_format,skills,experience_years,sections,contact_info)
            SELECT i,i,now(),'synthetic-resume.pdf',repeat('Synthetic Python PostgreSQL project experience. ',256),decode('73796e746865746963','hex'),'pdf','["Python","PostgreSQL","REST"]',3,'{"experience":"Fictional engineering evidence only."}','{"name":"Fictional Candidate"}' FROM generate_series(1,10000)i""")
        # Actor8 is the one heavy account: 9,999*10 + 500 = 100,490 opportunities.
        connection.exec_driver_sql("""INSERT INTO job_matches(id,user_id,resume_id,created_at,job_title,company,job_description,required_skills,full_matches,partial_matches,true_gaps,match_score,fit_summary,dimension_scores,skill_verification_rate,improvement_tips)
            SELECT i,CASE WHEN i<=99990 THEN CASE WHEN ((i-1)/10)+1=8 THEN 10000 ELSE ((i-1)/10)+1 END ELSE 8 END,
            CASE WHEN i<=99990 THEN CASE WHEN ((i-1)/10)+1=8 THEN 10000 ELSE ((i-1)/10)+1 END ELSE 8 END,
            now(),'Backend Engineer','Fictional Employer '||mod(i,20),repeat('Synthetic Python PostgreSQL REST distributed systems role. ',40),
            '["Python","PostgreSQL","REST","Distributed systems"]','["Python","PostgreSQL"]','[]','["Distributed systems"]',75,'Synthetic match summary','[]',0.5,'[]'
            FROM generate_series(1,100490)i""")
        connection.exec_driver_sql("""INSERT INTO opportunities(id,user_id,resume_id,latest_match_id,title,company,location,source,job_description,job_snapshot,stage,priority,notes,created_at,updated_at)
            SELECT 'capacity-opp-'||id,user_id,resume_id,id,job_title,company,'Bengaluru','manual',job_description,
            '{"required_skills":["Python","PostgreSQL","REST","Distributed systems"]}','saved','medium','',now(),now()-id*interval '1 second'
            FROM job_matches""")
        connection.exec_driver_sql("""INSERT INTO evidence_items(id,user_id,resume_id,category,title,evidence_text,metrics,skills,provenance,approval_state,confidence,created_at,updated_at)
            SELECT 'capacity-evidence-'||i||'-'||j,i,i,'experience','Synthetic engineering','Built a fictional Python service.','{}','["Python","PostgreSQL"]','user','approved','user_provided',now(),now()
            FROM generate_series(1,10000)i CROSS JOIN generate_series(1,3)j""")
        # Existing synthetic lots are data-volume inputs, never actual payments,
        # receipts, funded model allowances or permission to enable sales.
        connection.exec_driver_sql("INSERT INTO credit_accounts(user_id,debt_credits,created_at) SELECT i,0,now() FROM generate_series(1,10000)i WHERE mod(i," + str(divisor) + ")=0")
        connection.exec_driver_sql("""INSERT INTO credit_grants(id,user_id,source_type,source_id,origin_type,origin_id,funding_kind,quantity,remaining_credits,expired_credits,issued_at,expires_at,terms_snapshot)
            SELECT 'capacity-grant-'||i||'-'||j,i,'synthetic','capacity-source-'||i||'-'||j,'synthetic','capacity-origin-'||i||'-'||j,'paid',40,30,0,now()-j*interval '1 day',now()+(30-j)*interval '1 day','{"synthetic":true}'
            FROM generate_series(1,10000)i CROSS JOIN generate_series(1,3)j WHERE mod(i,""" + str(divisor) + ")=0")
        connection.exec_driver_sql("""INSERT INTO credit_reservations(id,user_id,operation,source_id,idempotency_key,input_fingerprint,quote_snapshot,execution_allowance_minor,reserved_credits,committed_credits,released_credits,state,created_at)
            SELECT 'capacity-reserve-'||i,i,'interview_questions','capacity-source-'||i,'capacity-key-'||i,repeat('a',64),'{}',225,5,0,0,'reserved',now()
            FROM generate_series(1,10000)i WHERE mod(i,""" + str(divisor) + ")=0")
        names = ("users", "resumes", "job_matches", "opportunities", "evidence_items",
                 "credit_accounts", "credit_grants", "credit_reservations", "candidate_password_accounts")
        counts = {}
        for table in names:
            connection.exec_driver_sql('ANALYZE "' + table + '"')
            counts[table] = connection.execute(text('SELECT count(*) FROM "' + table + '"')).scalar_one()
        counts["relation_bytes"] = int(connection.execute(text("""SELECT sum(pg_total_relation_size(c.oid))
            FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
            WHERE n.nspname='stage5_owned' AND c.relkind='r'""")).scalar_one())
        assert counts["users"] == counts["resumes"] == 10_000
        assert counts["opportunities"] == counts["job_matches"] == 100_490
        assert counts["credit_accounts"] == plan.users * plan.buyers_percent // 100
        return counts
