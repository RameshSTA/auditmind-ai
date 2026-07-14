"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { bffPost } from "@/lib/client-api";
import { SIGNUP_ROLES } from "@/lib/types";
import { ErrorNotice } from "@/components/ui";

type Mode = "signin" | "create";

export function LoginForm({
  initialMode,
  demoEmails,
  demoPassword,
}: {
  initialMode: Mode;
  demoEmails: readonly string[];
  demoPassword: string;
}) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [showDemo, setShowDemo] = useState(false);

  return (
    <div className="mx-auto mt-6 max-w-[440px]">
      <div className="card p-0 overflow-hidden">
        <div className="flex border-b border-line">
          <button
            type="button"
            className={`flex-1 py-3 text-sm font-medium ${mode === "signin" ? "border-b-2 border-ink text-ink" : "text-ink-secondary"}`}
            style={mode === "signin" ? { marginBottom: "-1px" } : undefined}
            onClick={() => setMode("signin")}
          >
            Sign in
          </button>
          <button
            type="button"
            className={`flex-1 py-3 text-sm font-medium ${mode === "create" ? "border-b-2 border-ink text-ink" : "text-ink-secondary"}`}
            style={mode === "create" ? { marginBottom: "-1px" } : undefined}
            onClick={() => setMode("create")}
          >
            Create account
          </button>
        </div>
        <div className="p-6">{mode === "signin" ? <SignInForm /> : <CreateAccountForm />}</div>
      </div>

      <div className="mt-4 text-center">
        <button
          type="button"
          className="muted underline decoration-dotted underline-offset-2"
          onClick={() => setShowDemo((v) => !v)}
        >
          {showDemo ? "Hide demo accounts" : "Want to explore first? Use a demo account"}
        </button>
        {showDemo ? (
          <div className="fade-in card mt-3 text-left">
            <p className="muted">
              Every demo account is a real, seeded sign-in — password <span className="mono">{demoPassword}</span>
            </p>
            <ul className="mono mt-2 grid gap-1 text-xs text-ink-secondary">
              {demoEmails.map((email) => (
                <li key={email}>{email}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function SignInForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const signIn = useMutation({
    mutationFn: () => bffPost<{ ok: true }>("/api/auth/login", { email, password }),
    onSuccess: () => {
      window.location.href = "/engagements";
    },
  });

  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (email.trim() && password) signIn.mutate();
      }}
    >
      <div>
        <h2 className="text-lg">Welcome back</h2>
        <p className="muted mt-1">Sign in with your work email.</p>
      </div>
      <label className="grid gap-1 text-xs font-medium text-ink-secondary">
        Email
        <input
          className="field"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
      </label>
      <label className="grid gap-1 text-xs font-medium text-ink-secondary">
        Password
        <input
          className="field"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>
      <button className="btn btn-primary mt-1" type="submit" disabled={signIn.isPending}>
        {signIn.isPending ? "Signing in…" : "Sign in"}
      </button>
      {signIn.error ? <ErrorNotice error={signIn.error} /> : null}
    </form>
  );
}

function CreateAccountForm() {
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState(SIGNUP_ROLES[0]?.value ?? "Auditor");

  const register = useMutation({
    mutationFn: () =>
      bffPost<{ ok: true }>("/api/auth/register", {
        email,
        password,
        display_name: displayName,
        role,
      }),
    onSuccess: () => {
      window.location.href = "/engagements";
    },
  });

  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (displayName.trim() && email.trim() && password) register.mutate();
      }}
    >
      <div>
        <h2 className="text-lg">Create your account</h2>
        <p className="muted mt-1">Tell us your role — it decides what you can do here.</p>
      </div>
      <label className="grid gap-1 text-xs font-medium text-ink-secondary">
        Full name
        <input
          className="field"
          autoComplete="name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          required
        />
      </label>
      <label className="grid gap-1 text-xs font-medium text-ink-secondary">
        Work email
        <input
          className="field"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
      </label>
      <label className="grid gap-1 text-xs font-medium text-ink-secondary">
        Password
        <input
          className="field"
          type="password"
          autoComplete="new-password"
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>
      <fieldset className="grid gap-1.5">
        <legend className="mb-1 text-xs font-medium text-ink-secondary">Your role</legend>
        {SIGNUP_ROLES.map((r) => (
          <label
            key={r.value}
            className={`flex cursor-pointer items-start gap-2.5 rounded border px-3 py-2 text-sm ${role === r.value ? "border-ink" : "border-line-strong"}`}
          >
            <input
              className="mt-1"
              type="radio"
              name="role"
              value={r.value}
              checked={role === r.value}
              onChange={() => setRole(r.value)}
            />
            <span>
              <span className="block font-medium">{r.label}</span>
              <span className="muted block">{r.description}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <button className="btn btn-primary mt-1" type="submit" disabled={register.isPending}>
        {register.isPending ? "Creating account…" : "Create account"}
      </button>
      {register.error ? <ErrorNotice error={register.error} /> : null}
    </form>
  );
}
