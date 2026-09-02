# AWS ECS handoff

이 Terraform은 배포 계약만 만든다. 기본 `enable_services=false`라 ECS task definition까지 검토할 수 있지만 실행 서비스는 만들지 않는다. 실제 적용 전 다음이 필요하다.

1. 팀 AWS VPC와 private subnet ID
2. immutable ECR image URI
3. MongoDB Atlas URI, 세션/암호화 키, 내부 토큰, provider/API·wallet key를 각각 담은 Secrets Manager ARN
4. 공개 진입점(ALB/HTTPS), Route 53, WAF, 서비스별 security group 규칙 결정
5. 민감 원문 TTL 또는 수동 삭제 정책 재승인
6. 비용 발생 및 실제 배포 승인 후에만 `enable_services=true`

`plain_environment`에는 공개 가능한 설정만 넣는다. secret 값은 Terraform 변수, state, plan 출력에 직접 넣지 말고 Secrets Manager ARN만 전달한다.
