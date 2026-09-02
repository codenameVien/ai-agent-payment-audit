# 로컬 MongoDB

`compose.yml`은 Linux kernel이 MongoDB 지원 범위에 있는 호스트에서 사용한다.

```bash
cd /Users/vien/MyProjects/PBL
docker compose -f infra/docker/compose.yml up -d mongo
```

현재 이 Mac의 Docker Desktop VM은 MongoDB가 시작을 차단하는 Linux 6.19–7.0.13 범위다. [MongoDB 공식 production notes](https://www.mongodb.com/docs/manual/administration/production-notes/)상 이미지 버전 변경으로 해결되지 않으므로, Docker kernel이 갱신될 때까지 네이티브 MongoDB 8.3을 격리 실행하는 다음 명령을 사용한다.

```bash
cd /Users/vien/MyProjects/PBL
npm run test:mongo:local
```

이 스크립트는 `/private/tmp/pbl-mongo-test.*` 아래에 임시 데이터베이스를 만들고, 테스트가 끝나면 프로세스와 디렉터리를 정리한다. Atlas나 프로젝트 개발 데이터베이스에는 접근하지 않는다.
