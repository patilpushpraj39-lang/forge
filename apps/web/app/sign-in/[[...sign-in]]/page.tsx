import { SignIn } from "@clerk/nextjs";


export default function SignInPage() {
  if (!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY) {
    return <main><p>Reviewer authentication is not configured.</p></main>;
  }
  return <main className="auth-page"><SignIn /></main>;
}
