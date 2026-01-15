from pyrogram import Client

API_ID = int(input("Enter API_ID: "))
API_HASH = input("Enter API_HASH: ")

async def main():
    async with Client("assistant_session", api_id=API_ID, api_hash=API_HASH) as app:
        session_string = await app.export_session_string()
        print("\n" + "="*50)
        print("YOUR SESSION STRING:")
        print("="*50)
        print(session_string)
        print("="*50)
        print("\nSave this session string in your .env file as ASSISTANT_SESSION")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
