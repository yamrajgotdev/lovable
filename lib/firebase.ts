/**
 * Firebase Configuration and Initialization
 * Uses Firebase JS SDK for Phone Authentication
 */
import { initializeApp } from 'firebase/app';
import { getAuth, signInWithPhoneNumber, PhoneAuthProvider, signInWithCredential } from 'firebase/auth';

// Firebase configuration from environment variables
const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

// Check if Firebase is properly configured
export function isFirebaseConfigured(): boolean {
  return !!(firebaseConfig.apiKey && firebaseConfig.projectId && firebaseConfig.authDomain);
}

// Initialize Firebase app
let app: ReturnType<typeof initializeApp> | null = null;
let auth: ReturnType<typeof getAuth> | null = null;

export function getFirebaseApp() {
  if (!app && isFirebaseConfigured()) {
    app = initializeApp(firebaseConfig);
  }
  return app;
}

export function getFirebaseAuth() {
  if (!auth) {
    const firebaseApp = getFirebaseApp();
    if (firebaseApp) {
      auth = getAuth(firebaseApp);
    }
  }
  return auth;
}

// Store verification ID for OTP verification
let verificationId: string | null = null;

/**
 * Send OTP to phone number using Firebase
 * @param phoneNumber - Phone number with country code (e.g., +918273781021)
 * @param recaptchaVerifier - reCAPTCHA verifier instance
 */
export async function sendOTP(phoneNumber: string, recaptchaVerifier: any): Promise<{ success: boolean; error?: string }> {
  try {
    if (!isFirebaseConfigured()) {
      return { success: false, error: 'Firebase not configured. Check environment variables.' };
    }

    const auth = getFirebaseAuth();
    if (!auth) {
      return { success: false, error: 'Firebase auth not initialized' };
    }

    const confirmationResult = await signInWithPhoneNumber(auth, phoneNumber, recaptchaVerifier);
    verificationId = confirmationResult.verificationId;
    
    return { success: true };
  } catch (error: any) {
    console.error('Firebase sendOTP error:', error);
    return { 
      success: false, 
      error: error.message || 'Failed to send OTP. Please try again.' 
    };
  }
}

/**
 * Verify OTP code
 * @param code - OTP code from SMS
 * @returns idToken if successful
 */
export async function verifyOTP(code: string): Promise<{ success: boolean; idToken?: string; error?: string }> {
  try {
    if (!verificationId) {
      return { success: false, error: 'No verification in progress. Request OTP first.' };
    }

    const credential = PhoneAuthProvider.credential(verificationId, code);
    const auth = getFirebaseAuth();
    
    if (!auth) {
      return { success: false, error: 'Firebase auth not initialized' };
    }

    const result = await signInWithCredential(auth, credential);
    const idToken = await result.user.getIdToken();
    
    return { success: true, idToken };
  } catch (error: any) {
    console.error('Firebase verifyOTP error:', error);
    return { 
      success: false, 
      error: error.message || 'Invalid OTP code. Please try again.' 
    };
  }
}

/**
 * Send OTP using REST API (fallback if SDK fails)
 * This matches your existing firebase_auth.ts implementation
 */
export async function sendOTPREST(phone: string, recaptchaToken: string): Promise<{ sessionInfo: string }> {
  const apiKey = import.meta.env.VITE_FIREBASE_API_KEY;
  
  if (!apiKey) {
    throw new Error('Firebase API key not configured');
  }

  const url = `https://identitytoolkit.googleapis.com/v1/accounts:sendVerificationCode?key=${apiKey}`;
  
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      phoneNumber: `+91${phone}`,
      recaptchaToken: recaptchaToken,
    }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Failed to send OTP');
  }

  const data = await response.json();
  return { sessionInfo: data.sessionInfo };
}

/**
 * Verify OTP using REST API (fallback if SDK fails)
 */
export async function verifyOTPREST(sessionInfo: string, code: string): Promise<{ idToken: string }> {
  const apiKey = import.meta.env.VITE_FIREBASE_API_KEY;
  
  if (!apiKey) {
    throw new Error('Firebase API key not configured');
  }

  const url = `https://identitytoolkit.googleapis.com/v1/accounts:signInWithVerificationCode?key=${apiKey}`;
  
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      sessionInfo: sessionInfo,
      code: code,
    }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Invalid OTP');
  }

  const data = await response.json();
  return { idToken: data.idToken };
}
