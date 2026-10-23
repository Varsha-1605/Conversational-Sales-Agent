from pathlib import Path
import pandas as pd
import urllib.parse
import os
from sqlalchemy import create_engine
from dotenv import load_dotenv

# __file__ is 'scripts/ingest_input_data.py'
script_dir = Path(__file__).resolve().parent # points to scripts
project_root = script_dir.parents[0] # climbs up 1 level to project root

load_dotenv(project_root / ".env")

data_dir = project_root / "data" / "raw"

db_host = os.getenv("SQL_SERVER_HOST", "localhost")
db_port = os.getenv("SQL_SERVER_PORT", "1433")
db_user = os.getenv("SQL_ADMIN_USER")
db_password = os.getenv("SQL_ADMIN_PASSWORD")

# 1. Connect to Docker MSSQL Server
print("Connecting to MSSQL Database...")
connection_string = (
        f"DRIVER={{SQL Server}};"
        f"SERVER={db_host},{db_port};"
        f"DATABASE=master;"
        f"UID={db_user};"
        f"PWD={db_password};"
        f"Encrypt=no;"
        f"TrustServerCertificate=yes;"
    )

params = urllib.parse.quote_plus(connection_string)

engine = create_engine(
    f"mssql+pyodbc:///?odbc_connect={params}",
    use_setinputsizes=False,
)

# 2. Load each CSV and ingest it into a table with the same name
for csv_path in sorted(data_dir.glob("*.csv")):
    table_name = csv_path.stem
    print(f"Loading {csv_path.name} into {table_name}...")
    df = pd.read_csv(csv_path)
    df.to_sql(table_name, engine, if_exists='replace', index=False, schema='dbo')

print("Data ingestion complete!")