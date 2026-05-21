/**
 * Firebase Phone Auth wrappers using official SDK flow:
 * RecaptchaVerifier + signInWithPhoneNumber + confirmationResult.confirm(code)
 */

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY as string | undefined,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN as string | undefined,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID as string | undefined,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET as string | undefined,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID as string | undefined,
  appId: import.meta.env.VITE_FIREBASE_APP_ID as string | undefined,
};

type FirebaseSdk = {
  getApps: typeof import("firebase/app")["getApps"];
  getApp: typeof import("firebase/app")["getApp"];
  initializeApp: typeof import("firebase/app")["initializeApp"];
  getAuth: typeof import("firebase/auth")["getAuth"];
  RecaptchaVerifier: typeof import("firebase/auth")["RecaptchaVerifier"];
  signInWithPhoneNumber: typeof import("firebase/auth")["signInWithPhoneNumber"];
};

let sdkPromise: Promise<FirebaseSdk> | null = null;
let recaptchaVerifier: import("firebase/auth").RecaptchaVerifier | null = null;
let recaptchaContainerId: string | null = null;
let confirmationResult: import("firebase/auth").ConfirmationResult | null = null;

export function isFirebaseConfigured(): boolean {
  return !!(
    firebaseConfig.apiKey &&
    firebaseConfig.projectId &&
    firebaseConfig.authDomain &&
    firebaseConfig.appId
  );
}

function normalizePhone(phone: string): string {
  const digits = phone.replace(/\D/g, "");
  if (digits.length === 10) return `+91${digits}`;
  if (digits.length === 12 && digits.startsWith("91")) return `+${digits}`;
  return phone.startsWith("+") ? phone : `+${digits}`;
}

// Helper to convert Firebase errors to user-friendly messages
export function getFirebaseErrorMessage(error: any): string {
  const code = error?.code || "";
  const message = error?.message || "";
  
  // Firebase auth error codes
  switch (code) {
    case "auth/invalid-phone-number":
      return "Invalid phone number. Please enter a valid 10-digit number.";
    case "auth/invalid-verification-code":
    case "auth/wrong-password":
      return "Invalid OTP. Please try again.";
    case "auth/code-expired":
      return "OTP has expired. Please request a new one.";
    case "auth/too-many-requests":
      return "Too many attempts. Please wait a few minutes and try again.";
    case "auth/captcha-check-failed":
      return "Security check failed. Please refresh and try again.";
    case "auth/network-request-failed":
      return "Connection failed. Please check your internet and try again.";
    case "auth/quota-exceeded":
      return "SMS quota exceeded. Please try again later.";
    case "auth/user-disabled":
      return "This account has been disabled. Please contact support.";
    case "auth/user-not-found":
      return "Account not found. Please create an account first.";
    case "auth/phone-number-already-exists":
      return "This phone number is already registered. Please sign in instead.";
    default:
      // Check message content for common errors
      if (message.includes("network") || message.includes("fetch")) {
        return "Connection failed. Please check your internet and try again.";
      }
      if (message.includes("already") || message.includes("exists")) {
        return "You already have an account. Please sign in instead.";
      }
      return message || "Something went wrong. Please try again.";
  }
}

async function loadFirebaseSdk(): Promise<FirebaseSdk> {
  if (!sdkPromise) {
    sdkPromise = Promise.all([import("firebase/app"), import("firebase/auth")]).then(
      ([app, auth]) => ({
        getApps: app.getApps,
        getApp: app.getApp,
        initializeApp: app.initializeApp,
        getAuth: auth.getAuth,
        RecaptchaVerifier: auth.RecaptchaVerifier,
        signInWithPhoneNumber: auth.signInWithPhoneNumber,
      }),
    );
  }
  return sdkPromise;
}

async function getFirebaseAuthClient() {
  if (typeof window === "undefined") {
    throw new Error("Firebase auth is only available in the browser.");
  }
  if (!isFirebaseConfigured()) {
    throw new Error("Firebase is not configured. Set VITE_FIREBASE_* environment variables.");
  }

  const sdk = await loadFirebaseSdk();
  const app = sdk.getApps().length ? sdk.getApp() : sdk.initializeApp(firebaseConfig);
  const auth = sdk.getAuth(app);

  if (
    import.meta.env.DEV &&
    import.meta.env.VITE_FIREBASE_APP_VERIFICATION_DISABLED_FOR_TESTING === "true"
  ) {
    auth.settings.appVerificationDisabledForTesting = true;
  }

  return { sdk, auth };
}

export async function prepareFirebaseRecaptcha(containerId: string): Promise<void> {
  const { sdk, auth } = await getFirebaseAuthClient();

  if (recaptchaVerifier && recaptchaContainerId === containerId) {
    await recaptchaVerifier.render();
    return;
  }

  if (recaptchaVerifier) {
    recaptchaVerifier.clear();
    recaptchaVerifier = null;
  }

  recaptchaVerifier = new sdk.RecaptchaVerifier(auth, containerId, {
    size: "normal",
    callback: () => {
      // Challenge solved; send OTP can proceed.
    },
    "expired-callback": () => {
      // Challenge expired; user can retry.
    },
  });
  recaptchaContainerId = containerId;
  await recaptchaVerifier.render();
}

export async function firebaseSendOtp(
  phone: string,
  containerId = "recaptcha-container",
): Promise<void> {
  await prepareFirebaseRecaptcha(containerId);
  const { sdk, auth } = await getFirebaseAuthClient();
  if (!recaptchaVerifier) {
    throw new Error("reCAPTCHA is not initialized.");
  }

  try {
    confirmationResult = await sdk.signInWithPhoneNumber(
      auth,
      normalizePhone(phone),
      recaptchaVerifier,
    );
  } catch (error) {
    await resetFirebaseRecaptcha();
    // Convert to user-friendly error
    const friendlyMessage = getFirebaseErrorMessage(error);
    throw new Error(friendlyMessage);
  }
}

export async function firebaseConfirmOtp(
  code: string,
): Promise<{ idToken: string; phoneNumber: string }> {
  if (!confirmationResult) {
    throw new Error("No OTP request in progress. Please request OTP first.");
  }

  try {
    const result = await confirmationResult.confirm(code);
    const idToken = await result.user.getIdToken(true);
    const phoneNumber = result.user.phoneNumber || "";
    return { idToken, phoneNumber };
  } catch (error) {
    // Convert to user-friendly error
    const friendlyMessage = getFirebaseErrorMessage(error);
    throw new Error(friendlyMessage);
  }
}

export async function resetFirebaseRecaptcha(): Promise<void> {
  // Clear the existing verifier completely
  if (recaptchaVerifier) {
    try {
      recaptchaVerifier.clear();
    } catch {
      // Ignore clear errors
    }
    recaptchaVerifier = null;
    recaptchaContainerId = null;
  }
  // Also reset grecaptcha if available
  try {
    const maybeWindow = window as unknown as {
      grecaptcha?: { reset?: () => void };
    };
    maybeWindow.grecaptcha?.reset?.();
  } catch {
    // Ignore reset errors
  }
}

export async function createInvisibleRecaptcha(): Promise<import("firebase/auth").RecaptchaVerifier> {
  const { sdk, auth } = await getFirebaseAuthClient();
  // Clear any existing verifier first
  if (recaptchaVerifier) {
    try {
      recaptchaVerifier.clear();
    } catch {}
    recaptchaVerifier = null;
  }
  // Create new invisible verifier
  recaptchaVerifier = new sdk.RecaptchaVerifier(auth, document.body, {
    size: "invisible",
  });
  recaptchaContainerId = "invisible";
  return recaptchaVerifier;
}

export async function resendFirebaseOtp(phone: string): Promise<void> {
  const normalizedPhone = normalizePhone(phone);
  const { sdk, auth } = await getFirebaseAuthClient();
  
  // Clear old verifier and create fresh invisible one
  if (recaptchaVerifier) {
    try {
      recaptchaVerifier.clear();
    } catch {}
    recaptchaVerifier = null;
  }
  
  recaptchaVerifier = new sdk.RecaptchaVerifier(auth, document.body, {
    size: "invisible",
  });
  recaptchaContainerId = "invisible";
  
  // Send new OTP with fresh verifier
  try {
    confirmationResult = await sdk.signInWithPhoneNumber(auth, normalizedPhone, recaptchaVerifier);
  } catch (error) {
    // Convert to user-friendly error
    const friendlyMessage = getFirebaseErrorMessage(error);
    throw new Error(friendlyMessage);
  }
}

export function clearFirebaseOtpSession(): void {
  confirmationResult = null;
}
