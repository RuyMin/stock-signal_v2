#!/usr/bin/env bash
# 컨테이너별 격리 단위 테스트 실행기.
#
# 호스트에서 한 번에 돌리면 워커별 동일 이름 'core/' 패키지 sys.modules 충돌이 발생하므로,
# 각 서비스의 자체 컨테이너 안에서 자기 모듈 테스트만 실행한다.
#
# 사용:
#   ./scripts/run-unit-tests.sh                         # 모든 서비스 순차 실행
#   ./scripts/run-unit-tests.sh backend                 # backend만
#   ./scripts/run-unit-tests.sh data-collector          # data-collector만
#   ./scripts/run-unit-tests.sh backend crewai          # 여러 개 지정
#
# 종료 코드: 모두 통과 시 0, 하나라도 실패 시 1
set -uo pipefail

ALL_SERVICES=(backend scheduler data-collector telegram-notifier telegram-listener crewai)

if [[ $# -eq 0 ]]; then
  TARGETS=("${ALL_SERVICES[@]}")
else
  TARGETS=("$@")
fi

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.test.yml --env-file .env.dev"
ANY_FAIL=0

for svc in "${TARGETS[@]}"; do
  echo ""
  echo "========================================"
  echo "  Unit tests: ${svc}"
  echo "========================================"
  if ! ${COMPOSE} run --rm "${svc}-tests"; then
    echo "❌ ${svc} tests FAILED"
    ANY_FAIL=1
  else
    echo "✅ ${svc} tests passed"
  fi
done

if [[ ${ANY_FAIL} -ne 0 ]]; then
  echo ""
  echo "❌ Some service tests failed"
  exit 1
fi

echo ""
echo "✅ All targeted service tests passed"
