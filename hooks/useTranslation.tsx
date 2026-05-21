import { useEffect, useState, useCallback } from "react";
import { auth, type Language } from "@/lib/api";

type Translations = {
  [key: string]: string | Translations;
};

const translations = {
  en: {
    // Common
    appName: "RIDES4U",
    back: "Back",
    cancel: "Cancel",
    confirm: "Confirm",
    save: "Save",
    delete: "Delete",
    edit: "Edit",
    close: "Close",
    loading: "Loading...",
    error: "Error",
    success: "Success",
    
    // Auth
    signIn: "Sign in",
    signUp: "Sign up",
    signOut: "Sign out",
    phone: "Phone",
    name: "Name",
    otp: "OTP",
    verify: "Verify",
    sendOtp: "Send OTP",
    resendOtp: "Resend OTP",
    
    // Roles
    passenger: "Passenger",
    rider: "Driver",
    iAmPassenger: "I am a Passenger",
    iAmRider: "I am a Driver",
    startEarning: "Start earning",
    
    // Passenger Home
    pickup: "Pickup",
    dropLocation: "Drop location",
    drop: "Drop",
    whereFrom: "Where from?",
    whereTo: "Where to?",
    useCurrent: "Use current",
    vehicle: "Vehicle",
    bike: "Bike",
    auto: "Auto / Tempo",
    erickshaw: "E-Rickshaw",
    bikeSub: "Fastest in traffic",
    autoSub: "Best value for short trips",
    erickshawSub: "Eco friendly · affordable",
    bookRideTitle: "Book a ride",
    yourDriver: "Your Driver",
    fare: "Fare",
    total: "Total",
    requestRide: "Request ride",
    confirmRide: "Confirm ride",
    promoCode: "Promo code",
    addPromo: "Add promo code",
    addLocations: "Add locations",
    nearbyDrivers: "nearby",
    activeRide: "Active Ride",
    tapToView: "Tap to view ride details",
    cancelRide: "Cancel Ride",
    confirmCancelRide: "Are you sure you want to cancel this ride?",
    rideCancelled: "Ride cancelled",
    cancelFailed: "Cancel failed",
    rateYourDriver: "Rate your driver",
    baseFare: "Base fare",
    perKm: "Per km",
    tax: "Tax",
    apply: "Apply",
    applied: "Applied",
    confirming: "Confirming...",
    activeRideRedirect: "You have an active ride. Redirecting...",
    activeRideExists: "You have an active ride. Please complete or cancel it first.",
    requestFailed: "Request failed",
    
    // Driver Home
    goOnline: "Go Online",
    goOffline: "Go Offline",
    online: "Online",
    offline: "Offline",
    earnings: "Earnings",
    today: "Today",
    rides: "Rides",
    rating: "Rating",
    wallet: "Wallet",
    cash: "Cash",
    
    // Ride Status
    searchingDriver: "Searching for driver...",
    driverAssigned: "Driver assigned",
    driverArriving: "Driver is arriving",
    driverArrived: "Driver has arrived",
    rideStarted: "Ride in progress",
    reachedDestination: "Reached destination",
    payNow: "Pay now",
    
    // Navigation
    history: "History",
    account: "Account",
    settings: "Settings",
    saved: "Saved",
    support: "Support",
    
    // Language
    language: "Language",
    english: "English",
    hindi: "हिन्दी",
    chooseLanguage: "Choose your language",
    
    // Location
    locationPermission: "Please enable location permissions",
    gettingLocation: "Getting location...",
    
    // Errors
    noPickup: "Add pickup location",
    noDrop: "Add drop location",
    invalidPhone: "Invalid phone number",
    invalidOtp: "Invalid OTP",
    networkError: "Network error. Please try again.",
    
    // Time
    min: "min",
    km: "km",
    eta: "ETA",
    
    // Chat
    chat: "Chat",
    message: "Message",
    
    // Payment
    payment: "Payment",
    cashPayment: "Cash payment",
    onlinePayment: "Online payment",
    paymentSuccess: "Payment successful",
    paymentFailed: "Payment failed",
    shareCode: "Share this code with driver",
    
    // Rating
    rateRide: "Rate your ride",
    submitRating: "Submit rating",
    howWasRide: "How was your ride?",
    
    // Misc
    liveMap: "Live map",
    noDrivers: "No drivers nearby",
    rideHistory: "Ride history",
    noHistory: "No rides yet",
    comingSoon: "Coming soon",
    noSavedPlaces: "No saved places yet",
    manageSavedPlaces: "Manage saved places",
    labelAndAddressRequired: "Label and address required",
    couldNotSave: "Could not save",
    deleteFailed: "Delete failed",
    label: "Label",
    labelPlaceholder: "Home, Office…",
    address: "Address",
    searchAddress: "Search address…",
    savePlace: "Save place",
    remove: "Remove",
    home: "Home",
  },
  hi: {
    // Common
    appName: "RIDES4U",
    back: "वापस",
    cancel: "रद्द करें",
    confirm: "पुष्टि करें",
    save: "सहेजें",
    delete: "हटाएं",
    edit: "संपादित करें",
    close: "बंद करें",
    loading: "लोड हो रहा है...",
    error: "त्रुटि",
    success: "सफल",
    
    // Auth
    signIn: "साइन इन",
    signUp: "साइन अप",
    signOut: "साइन आउट",
    phone: "फ़ोन",
    name: "नाम",
    otp: "ओटीपी",
    verify: "सत्यापित करें",
    sendOtp: "ओटीपी भेजें",
    resendOtp: "ओटीपी फिर से भेजें",
    
    // Roles
    passenger: "यात्री",
    rider: "ड्राइवर",
    iAmPassenger: "मैं यात्री हूँ",
    iAmRider: "मैं ड्राइवर हूँ",
    startEarning: "कमाई शुरू करें",
    
    // Passenger Home
    whereFrom: "कहाँ से जाना है?",
    whereTo: "कहाँ जाना है?",
    useCurrent: "वर्तमान स्थान",
    vehicle: "वाहन",
    bike: "बाइक",
    auto: "ऑटो / टेंपो",
    erickshaw: "ई-रिक्शा",
    bikeSub: "ट्रैफिक में सबसे तेज़",
    autoSub: "छोटी यात्राओं के लिए बेहतर",
    erickshawSub: "इको फ्रेंडली · सस्ता",
    bookRideTitle: "सवारी बुक करें",
    yourDriver: "आपका ड्राइवर",
    pickup: "पिकअप",
    drop: "ड्रॉप",
    fare: "किराया",
    total: "कुल",
    requestRide: "सवारी अनुरोध",
    confirmRide: "सवारी पुष्टि",
    promoCode: "प्रोमो कोड",
    addPromo: "प्रोमो कोड डालें",
    addLocations: "स्थान जोड़ें",
    nearbyDrivers: "पास के",
    activeRide: "सक्रिय सवारी",
    tapToView: "विवरण देखने के लिए टैप करें",
    cancelRide: "सवारी रद्द करें",
    confirmCancelRide: "क्या आप वाकई इस सवारी को रद्द करना चाहते हैं?",
    rideCancelled: "सवारी रद्द हो गई",
    cancelFailed: "रद्द करने में विफल",
    rateYourDriver: "अपने ड्राइवर को रेट करें",
    baseFare: "बेस किराया",
    perKm: "प्रति किमी",
    tax: "कर",
    apply: "लागू करें",
    applied: "लागू किया गया",
    confirming: "पुष्टि हो रही है...",
    activeRideRedirect: "आपकी एक सक्रिय सवारी है। रीडायरेक्ट हो रहा है...",
    activeRideExists: "आपकी एक सक्रिय सवारी है। कृपया पहले उसे पूरा या रद्द करें।",
    requestFailed: "अनुरोध विफल",
    
    // Driver Home
    goOnline: "ऑनलाइन जाएं",
    goOffline: "ऑफलाइन जाएं",
    online: "ऑनलाइन",
    offline: "ऑफलाइन",
    earnings: "कमाई",
    today: "आज",
    rides: "सवारियाँ",
    rating: "रेटिंग",
    wallet: "वॉलेट",
    cash: "कैश",
    
    // Ride Status
    searchingDriver: "ड्राइवर खोज रहे हैं...",
    driverAssigned: "ड्राइवर नियुक्त",
    driverArriving: "ड्राइवर आ रहा है",
    driverArrived: "ड्राइवर पहुँच गया",
    rideStarted: "सवारी चल रही है",
    reachedDestination: "गंतव्य पर पहुँचे",
    payNow: "अभी भुगतान करें",
    
    // Navigation
    history: "इतिहास",
    account: "खाता",
    settings: "सेटिंग्स",
    saved: "सहेजे गए",
    support: "सहायता",
    
    // Language
    language: "भाषा",
    english: "English",
    hindi: "हिन्दी",
    chooseLanguage: "अपनी भाषा चुनें",
    
    // Location
    locationPermission: "कृपया लोकेशन अनुमति दें",
    gettingLocation: "लोकेशन मिल रहा है...",
    
    // Errors
    noPickup: "पिकअप स्थान डालें",
    noDrop: "ड्रॉप स्थान डालें",
    invalidPhone: "अमान्य फ़ोन नंबर",
    invalidOtp: "अमान्य ओटीपी",
    networkError: "नेटवर्क त्रुटि। फिर से प्रयास करें।",
    
    // Time
    min: "मिन",
    km: "किमी",
    eta: "पहुँचने का समय",
    
    // Chat
    chat: "चैट",
    message: "संदेश",
    
    // Payment
    payment: "भुगतान",
    cashPayment: "कैश भुगतान",
    onlinePayment: "ऑनलाइन भुगतान",
    paymentSuccess: "भुगतान सफल",
    paymentFailed: "भुगतान विफल",
    shareCode: "यह कोड ड्राइवर को दिखाएं",
    
    // Rating
    rateRide: "सवारी को रेट करें",
    submitRating: "रेटिंग भेजें",
    howWasRide: "आपकी सवारी कैसी रही?",
    
    // Misc
    liveMap: "लाइव मैप",
    noDrivers: "पास में कोई ड्राइवर नहीं",
    gettingQuote: "किराया जानकारी मिल रही है...",
    rideHistory: "सवारी का इतिहास",
    noHistory: "अभी तक कोई सवारी नहीं",
    comingSoon: "जल्द आ रहा है",
    noSavedPlaces: "अभी तक कोई सहेजे गए स्थान नहीं",
    manageSavedPlaces: "सहेजे गए स्थान प्रबंधित करें",
    labelAndAddressRequired: "लेबल और पता आवश्यक है",
    couldNotSave: "सहेज नहीं सका",
    deleteFailed: "हटाने में विफल",
    label: "लेबल",
    labelPlaceholder: "घर, ऑफिस…",
    address: "पता",
    searchAddress: "पता खोजें…",
    savePlace: "स्थान सहेजें",
    remove: "हटाएं",
    home: "होम",
  }
};

export function useTranslation() {
  const [lang, setLang] = useState<Language>(() => auth.language);
  
  useEffect(() => {
    const handleStorage = () => {
      setLang(auth.language);
    };
    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
  }, []);

  const t = useCallback((key: string): string => {
    const keys = key.split(".");
    let value: any = translations[lang];
    
    for (const k of keys) {
      if (value && typeof value === "object" && k in value) {
        value = value[k];
      } else {
        // Fallback to English if key not found
        value = translations["en"];
        for (const k2 of keys) {
          if (value && typeof value === "object" && k2 in value) {
            value = value[k2];
          } else {
            return key; // Return key as fallback
          }
        }
        break;
      }
    }
    
    return typeof value === "string" ? value : key;
  }, [lang]);

  const setLanguage = useCallback((newLang: Language) => {
    auth.setLanguage(newLang);
    setLang(newLang);
    // Notify other components
    window.dispatchEvent(new StorageEvent("storage", { key: "rides4u_lang" }));
  }, []);

  return { t, lang, setLanguage };
}
