"""
Benchmark script for measuring pipeline performance on scale large.
"""
import time
import tempfile
from pathlib import Path
from solution.pipeline import Pipeline

def run_benchmark():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "large.db"
        rep_dir = Path(tmpdir) / "reports_large"
        
        pipeline = Pipeline(db_path=db_file)
        
        t0 = time.perf_counter()
        print("Ingesting data_large/drop_1...")
        res1 = pipeline.ingest_drop("data_large/drop_1")
        t1 = time.perf_counter()
        print(f"drop_1 ingested in {t1 - t0:.2f}s ({res1['leads_rows']} leads, {res1['events_lines']} events)")
        
        print("Ingesting data_large/drop_2...")
        res2 = pipeline.ingest_drop("data_large/drop_2")
        t2 = time.perf_counter()
        print(f"drop_2 ingested in {t2 - t1:.2f}s ({res2['leads_rows']} leads, {res2['events_lines']} events)")
        
        print("Generating reports...")
        pipeline.generate_reports(rep_dir)
        t3 = time.perf_counter()
        print(f"Reports generated in {t3 - t2:.2f}s")
        
        total_time = t3 - t0
        print(f"\n==========================================")
        print(f"TOTAL PIPELINE TIME: {total_time:.2f}s ({total_time / 60:.2f} min)")
        print(f"==========================================")

if __name__ == "__main__":
    run_benchmark()

