import os

from dotenv import load_dotenv
load_dotenv(dotenv_path="D:\\Downloads\\Python programming\\QR code project/.env")


from sqlalchemy import create_engine, text

DATABASE_URL="postgresql://postgres.yjakxhhmesbncbxmibfp:m4JFPkWCxOtBayj7@aws-1-ap-northeast-2.pooler.supabase.com:6543/postgres"


engine = create_engine(DATABASE_URL)

def get_query_result(sql_query):
    with engine.connect() as conn:
        result = conn.execute(text(sql_query))
        return result.fetchall()
    
def execute_query(sql_query):
    with engine.connect() as conn:
        conn.execute(text(sql_query))
        conn.commit()
        return True
    
sql_query = "SELECT qr_unique_id,is_sold,is_activated,user_id,activated_at,created_at FROM public.\"qr_codes\" order by created_at desc;"
#sql_query = "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'qr_codes' AND column_name IS NOT NULL ORDER BY ordinal_position;"
#result = get_query_result(sql_query)

#print("Query Result:")
#for row in result:
#    print(row)
    
print("AUTH:", os.getenv("MSG91_AUTH_KEY"))
print("FLOW:", os.getenv("MSG91_FLOW_ID"))