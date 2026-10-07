# JWT guards the business routes. Browser preflight has no token and uses
# API Gateway's automatic CORS response without invoking the application.
resource "aws_apigatewayv2_route" "preflight" {
  count = var.deploy_api ? 1 : 0
  api_id = aws_apigatewayv2_api.app[0].id
  route_key = "OPTIONS /api/v1/{proxy+}"
  authorization_type = "NONE"
  target = "integrations/${aws_apigatewayv2_integration.api[0].id}"
}
