#!/usr/bin/env python3
"""프록시 연결 테스트 — MaskProxy + DataImpulse.

사용법:
  cd /opt/workspace/ebooklib/apps/backend
  python scripts/test_proxy.py
"""

import sys
import os

# backend 디렉토리를 path에 추가
backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "apps", "backend")
sys.path.insert(0, os.path.abspath(backend_dir))


def test_maskproxy_connection():
    """MaskProxy 연결 테스트."""
    from lib.proxy_session import get_maskproxy_config, create_proxy_session

    config = get_maskproxy_config()
    if not config.is_configured:
        print("⏭️  MaskProxy 미설정 - 스킵")
        return True

    print(f"🔍 MaskProxy 연결 테스트: {config.host}:{config.port}")
    session = create_proxy_session("chrome131", config)

    try:
        resp = session.get("https://httpbin.org/ip", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            ip = data.get("origin", "unknown")
            print(f"✅ MaskProxy 성공 - IP: {ip}")
            return True
        else:
            print(f"❌ MaskProxy 실패 - HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ MaskProxy 에러 - {e}")
        return False


def test_dataimpulse_connection():
    """DataImpulse 연결 테스트."""
    from lib.proxy_session import get_dataimpulse_config, create_proxy_session

    config = get_dataimpulse_config()
    if not config.is_configured:
        print("⏭️  DataImpulse 미설정 - 스킵")
        return True

    print(f"🔍 DataImpulse 연결 테스트: {config.host}:{config.port}")
    session = create_proxy_session("chrome131", config)

    try:
        resp = session.get("https://httpbin.org/ip", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            ip = data.get("origin", "unknown")
            print(f"✅ DataImpulse 성공 - IP: {ip}")
            return True
        else:
            print(f"❌ DataImpulse 실패 - HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ DataImpulse 에러 - {e}")
        return False


def test_toki31_with_proxy():
    """프록시를 통한 toki31 접근 테스트."""
    from lib.proxy_session import get_proxy_session_with_fallback

    session, proxy_config = get_proxy_session_with_fallback()
    if session is None:
        print("⏭️  프록시 미설정 - toki31 테스트 스킵")
        return True

    print(f"🔍 toki31 프록시 연결 테스트 ({proxy_config.name})")
    try:
        resp = session.get("https://toki31.com/novel", timeout=15)
        if resp.status_code == 200:
            print(f"✅ toki31 성공 - {len(resp.text)} bytes")
            return True
        else:
            print(f"❌ toki31 실패 - HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ toki31 에러 - {e}")
        return False


def main():
    """메인 테스트 실행."""
    print("=" * 50)
    print("프록시 연결 테스트")
    print("=" * 50)

    results = []

    # MaskProxy 테스트
    results.append(("MaskProxy", test_maskproxy_connection()))
    print()

    # DataImpulse 테스트
    results.append(("DataImpulse", test_dataimpulse_connection()))
    print()

    # toki31 프록시 테스트
    results.append(("toki31", test_toki31_with_proxy()))
    print()

    # 결과 요약
    print("=" * 50)
    print("테스트 결과 요약")
    print("=" * 50)

    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {name}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("✅ 모든 테스트 통과")
        return 0
    else:
        print("❌ 일부 테스트 실패")
        return 1


if __name__ == "__main__":
    sys.exit(main())
