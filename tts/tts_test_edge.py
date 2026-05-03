"""
Edge TTS 한국어 목소리 비교 테스트
--------------------------------------
설치: pip install edge-tts
실행: python tts_test_edge.py

output 폴더에 목소리별 mp3 파일이 생성됩니다.
"""

import asyncio
import edge_tts
import os

# ✅ 테스트할 나레이션 텍스트 (실제 Shorts 나레이션으로 교체하세요)
TEST_TEXT = """
지금 로봇 산업이 완전히 바뀌고 있습니다.
피규어, 테슬라, 보스턴 다이나믹스.
이 세 회사가 만들어낼 미래, 지금 바로 확인해보세요.
"""

# ✅ 테스트할 한국어 목소리 목록 (edge-tts --list-voices 기준)
VOICES = [
    ("ko-KR-HyunsuMultilingualNeural", "현수 다국어 (남성)"),
    ("ko-KR-InJoonNeural", "인준 (남성)"),
    ("ko-KR-SunHiNeural", "선히 (여성)"),
]

OUTPUT_DIR = "tts_samples"
os.makedirs(OUTPUT_DIR, exist_ok=True)


async def generate(voice_id: str, label: str):
    filename = os.path.join(OUTPUT_DIR, f"{voice_id}.mp3")
    print(f"생성 중: {label} ({voice_id})")
    communicate = edge_tts.Communicate(TEST_TEXT, voice_id, rate="+5%")
    await communicate.save(filename)
    print(f"  → 저장: {filename}")


async def main():
    print("=== Edge TTS 한국어 목소리 비교 테스트 ===\n")
    # 동시 요청 다건은 서버에서 오디오 미수신(NoAudioReceived)이 날 수 있어 순차 실행
    for v, l in VOICES:
        await generate(v, l)
    print(f"\n완료! '{OUTPUT_DIR}' 폴더에서 파일 확인하세요.")
    print("목소리 목록:")
    for v, l in VOICES:
        print(f"  {l:20s} → {v}.mp3")


if __name__ == "__main__":
    asyncio.run(main())
