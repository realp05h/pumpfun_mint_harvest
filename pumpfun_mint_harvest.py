import asyncio
import websockets
import json
import base64
import struct
import csv
from solders.pubkey import Pubkey
from solana.rpc.api import Client
from datetime import datetime, timedelta
import requests
import os
import sys
import time
import io

# Constants
WSS = "wss://mainnet.helius-rpc.com/?api-key=YOUR_API_KEY"  # Replace with your Helius API key
SOLANA_RPC_URL = "https://YOUR_RPC_ENDPOINT"  # Replace with your RPC endpoint
CSV_FILE_PATH = "token_logs.csv"

# Initialize Solana RPC client
solana_client = Client(SOLANA_RPC_URL)

# Retry settings
max_retries = 20
restart_delay = 5
backoff_factor = 3

def get_current_time():
    """Get the current timestamp as a string."""
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

def parse_event_data(data_hex):
    """Parse the token creation event data."""
    try:
        data_bytes = bytes.fromhex(data_hex)
        offset = 8

        def read_length_prefixed_string(data, offset):
            length = struct.unpack('<I', data[offset:offset + 4])[0]
            offset += 4
            string_data = data[offset:offset + length]
            offset += length
            return string_data.decode('utf-8').strip('\x00'), offset

        def read_pubkey(data, offset):
            pubkey_data = data[offset:offset + 32]
            offset += 32
            pubkey = str(Pubkey.from_bytes(pubkey_data))
            return pubkey, offset

        event_data = {}
        event_data['name'], offset = read_length_prefixed_string(data_bytes, offset)
        event_data['symbol'], offset = read_length_prefixed_string(data_bytes, offset)
        event_data['uri'], offset = read_length_prefixed_string(data_bytes, offset)
        event_data['mint'], offset = read_pubkey(data_bytes, offset)
        event_data['bonding_curve'], offset = read_pubkey(data_bytes, offset)
        event_data['user'], offset = read_pubkey(data_bytes, offset)

        return event_data
    except Exception as e:
        print(f"Error parsing event data: {e}")
        raise

def fetch_token_metadata(uri):
    """
    Fetch and parse metadata from token URI.
    Returns a dictionary with image and social links.
    """
    try:
        response = requests.get(uri, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        metadata = {
            'image': data.get('image', 'NA'),
            'twitter': data.get('twitter', 'NA'),
            'telegram': data.get('telegram', 'NA'),
            'website': data.get('website', 'NA')
        }
        
        # Clean up social links if they exist
        if metadata['twitter'] != 'NA' and not metadata['twitter'].startswith(('http://', 'https://')):
            metadata['twitter'] = f"https://twitter.com/{metadata['twitter'].lstrip('@')}"
            
        if metadata['telegram'] != 'NA' and not metadata['telegram'].startswith(('http://', 'https://')):
            metadata['telegram'] = f"https://t.me/{metadata['telegram'].lstrip('@')}"
            
        if metadata['website'] != 'NA' and not metadata['website'].startswith(('http://', 'https://')):
            metadata['website'] = f"https://{metadata['website']}"
            
        return metadata
        
    except Exception as e:
        print(f"Error fetching metadata from {uri}: {e}")
        return {
            'image': 'NA',
            'twitter': 'NA',
            'telegram': 'NA',
            'website': 'NA'
        }

def write_to_csv(data):
    """Write token data to CSV file."""
    try:
        with open(CSV_FILE_PATH, mode='a', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=data.keys())
            if file.tell() == 0:
                writer.writeheader()
            writer.writerow(data)
        print(f"Successfully logged token: {data['name']} ({data['symbol']})")
    except Exception as e:
        print(f"Error writing to CSV: {e}")

def process_logs_response(response):
    """Process WebSocket log responses."""
    try:
        log_data = json.loads(response)
        logs = log_data.get("params", {}).get("result", {}).get("value", {}).get("logs", [])

        if "Instruction: InitializeMint2" in ''.join(logs):
            for log_entry in logs:
                if "Program data: " in log_entry and not log_entry.startswith("Program data: vdt/"):
                    program_data_base64 = log_entry.split("Program data: ")[1]
                    program_data_hex = base64.b64decode(program_data_base64).hex()
                    event_data = parse_event_data(program_data_hex)
                    
                    # Add timestamp
                    event_data['mint_time'] = get_current_time()
                    
                    # Fetch and add metadata
                    print(f"Fetching metadata for {event_data['name']}...")
                    metadata = fetch_token_metadata(event_data['uri'])
                    event_data.update(metadata)
                    
                    # Log the complete token data
                    write_to_csv(event_data)
                    
    except Exception as e:
        print(f"Error processing logs: {e}")

async def subscribe_to_logs(websocket):
    """Subscribe to token creation logs."""
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "logsSubscribe",
        "params": [
            {"mentions": ["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"]},
            {"commitment": "processed"}
        ]
    }

    await websocket.send(json.dumps(request))
    print("Subscribed to token creation logs...")

    while True:
        try:
            response = await websocket.recv()
            process_logs_response(response)
        except Exception as e:
            print(f"Error in log subscription: {e}")
            break

async def logs_subscribe():
    """Main WebSocket connection handler."""
    retry_count = 0

    while True:
        try:
            print("Connecting to WebSocket...")
            async with websockets.connect(WSS, ping_interval=60, ping_timeout=30) as websocket:
                await subscribe_to_logs(websocket)
                retry_count = 0
        except Exception as e:
            print(f"WebSocket error: {e}")
            retry_count += 1
            
            if retry_count > max_retries:
                print("Maximum retries reached. Exiting...")
                break

            sleep_time = min(30, backoff_factor ** retry_count + (retry_count % 2))
            print(f"Retrying in {sleep_time} seconds...")
            await asyncio.sleep(sleep_time)

async def main():
    """Main program loop."""
    try:
        tasks = [
            asyncio.create_task(logs_subscribe())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    except Exception as e:
        print(f"Critical error: {e}")
        restart_script()

def restart_script():
    """Restart the script after a delay."""
    print(f"Restarting in {restart_delay} seconds...")
    time.sleep(restart_delay)
    os.execv(sys.executable, ['python'] + sys.argv)

if __name__ == "__main__":
    print("Starting the p05h PumpFun Token Monitor...")
    while True:
        try:
            asyncio.run(main())
        except Exception as e:
            print(f"Script crashed: {e}")
            time.sleep(restart_delay)
