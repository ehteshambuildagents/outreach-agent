"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect } from "react";
import { setApiTokenProvider } from "@/lib/api";

export function ApiAuthBridge() {
  const { getToken, isLoaded, isSignedIn } = useAuth();

  useEffect(() => {
    if (!isLoaded || !isSignedIn) {
      setApiTokenProvider(null);
      return;
    }

    setApiTokenProvider(() => getToken());
    return () => setApiTokenProvider(null);
  }, [getToken, isLoaded, isSignedIn]);

  return null;
}
