/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend base URL, e.g. https://abc.lambda-url.us-east-1.on.aws (empty: same origin). */
  readonly VITE_API_URL?: string
  /** Cognito user pool settings; no client ID = auth disabled (local development). */
  readonly VITE_COGNITO_REGION?: string
  readonly VITE_COGNITO_USER_POOL_ID?: string
  readonly VITE_COGNITO_CLIENT_ID?: string
  readonly VITE_COGNITO_DOMAIN?: string
  /** "true" once Google is set up as an identity provider in the user pool. */
  readonly VITE_COGNITO_GOOGLE?: string
  /** Agenda parser function URL (infra/ai.yml); empty = no Paste an agenda box. */
  readonly VITE_PARSER_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
