import sys
import time
from src.autocomplete.snapshot_loader import load_snapshot

def main():
    snapshot_path = sys.argv[1] if len(sys.argv) > 1 else "artifacts/snapshot_v1"
    
    print(f"Loading snapshot from '{snapshot_path}'...")
    start_t = time.perf_counter()
    index = load_snapshot(snapshot_path)
    load_ms = (time.perf_counter() - start_t) * 1000
    print(f"Index loaded successfully in {load_ms:.2f} ms!\n")
    
    print("Type a prefix/query to search (or 'exit' / 'quit' to stop):")
    print("-" * 60)
    
    while True:
        try:
            query = input("\nSearch > ").strip()
            if not query:
                continue
            if query.lower() in ("exit", "quit"):
                break
            
            t0 = time.perf_counter()
            results = index.search(query, limit=10)
            latency_ms = (time.perf_counter() - t0) * 1000
            
            print(f"Found {len(results)} matches in {latency_ms:.3f} ms:")
            for i, res in enumerate(results, 1):
                print(f"  {i:2d}. {res}")
                
        except (KeyboardInterrupt, EOFError):
            print("\nExiting search.")
            break

if __name__ == "__main__":
    main()
