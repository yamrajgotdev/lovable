import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Btn } from "@/components/Field";
import { X, MapPin, Navigation, Star, Wallet, MessageSquare, Headphones, Car, DollarSign, Bell, Shield, Clock, ChevronRight, ChevronLeft, Check } from "lucide-react";

interface OnboardingModalProps {
  isOpen: boolean;
  onClose: () => void;
  userType: "driver" | "passenger";
  isFirstTime?: boolean;
}

type Slide = {
  icon: React.ReactNode;
  title: string;
  description: string;
  features?: string[];
};

const passengerSlides: Slide[] = [
  {
    icon: <MapPin className="w-12 h-12 text-primary" />,
    title: "Book a Ride",
    description: "Request rides instantly from anywhere. Choose your pickup and drop locations, select your preferred vehicle type.",
    features: ["Real-time driver tracking", "Estimated arrival times", "Multiple vehicle options"]
  },
  {
    icon: <Navigation className="w-12 h-12 text-emerald-500" />,
    title: "Track Your Driver",
    description: "Watch your driver approach in real-time on the map. Get notified when they arrive.",
    features: ["Live location updates", "Driver details & photo", "Direct call option"]
  },
  {
    icon: <Star className="w-12 h-12 text-amber-500" />,
    title: "Rate & Review",
    description: "Share your experience after each ride. Help us maintain quality service.",
    features: ["Star ratings", "Anonymous feedback", "Driver rewards"]
  },
  {
    icon: <MapPin className="w-12 h-12 text-blue-500" />,
    title: "Saved Places",
    description: "Save your frequent destinations for quick booking. Home, work, or any favorite spot.",
    features: ["One-tap booking", "Address suggestions", "Recent locations"]
  },
  {
    icon: <Headphones className="w-12 h-12 text-rose-500" />,
    title: "24/7 Support",
    description: "Need help? Our support team is available round the clock. Report issues, get assistance.",
    features: ["In-app chat support", "Issue tracking", "Quick resolution"]
  },
  {
    icon: <Wallet className="w-12 h-12 text-violet-500" />,
    title: "Flexible Payments",
    description: "Pay with cash or online. Safe, secure, and convenient payment options.",
    features: ["Cash or online", "Digital wallet", "Receipts & history"]
  }
];

const driverSlides: Slide[] = [
  {
    icon: <Car className="w-12 h-12 text-primary" />,
    title: "Go Online",
    description: "Start receiving ride requests by going online. Toggle your availability anytime.",
    features: ["One-tap online/offline", "Smart ride matching", "Nearby requests"]
  },
  {
    icon: <Navigation className="w-12 h-12 text-emerald-500" />,
    title: "Navigation",
    description: "Built-in navigation helps you reach pickup and drop locations efficiently.",
    features: ["Turn-by-turn directions", "Traffic updates", "Optimal routes"]
  },
  {
    icon: <DollarSign className="w-12 h-12 text-green-500" />,
    title: "Earnings & Wallet",
    description: "Track your daily, weekly, and monthly earnings. Withdraw to your bank anytime.",
    features: ["Real-time earnings", "Instant withdrawal", "Bonus & incentives"]
  },
  {
    icon: <Star className="w-12 h-12 text-amber-500" />,
    title: "Ratings & Performance",
    description: "Monitor your rating and performance stats. Great service gets rewarded.",
    features: ["Customer ratings", "Performance insights", "Achievement badges"]
  },
  {
    icon: <Clock className="w-12 h-12 text-blue-500" />,
    title: "Ride History",
    description: "View your complete ride history. Track completed trips and earnings per ride.",
    features: ["Detailed trip logs", "Earnings breakdown", "Receipts"]
  },
  {
    icon: <Headphones className="w-12 h-12 text-rose-500" />,
    title: "Driver Support",
    description: "Get help whenever you need. Report issues with rides, payments, or app.",
    features: ["24/7 support team", "Priority assistance", "Issue resolution"]
  }
];

export function OnboardingModal({ isOpen, onClose, userType, isFirstTime = false }: OnboardingModalProps) {
  const [currentSlide, setCurrentSlide] = useState(0);
  const slides = userType === "driver" ? driverSlides : passengerSlides;

  useEffect(() => {
    if (isOpen) {
      setCurrentSlide(0);
    }
  }, [isOpen]);

  const nextSlide = () => {
    if (currentSlide < slides.length - 1) {
      setCurrentSlide(prev => prev + 1);
    } else {
      onClose();
    }
  };

  const prevSlide = () => {
    if (currentSlide > 0) {
      setCurrentSlide(prev => prev - 1);
    }
  };

  const skip = () => {
    onClose();
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={skip}
            className="fixed inset-0 bg-black/60 z-[9998]"
          />

          {/* Modal */}
          <motion.div
            initial={{ opacity: 0, scale: 0.9, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.9, y: 20 }}
            className="fixed inset-4 sm:inset-auto sm:top-1/2 sm:left-1/2 sm:-translate-x-1/2 sm:-translate-y-1/2 sm:w-full sm:max-w-md sm:max-h-[90vh] bg-background rounded-3xl z-[9999] overflow-hidden flex flex-col border border-border shadow-2xl"
          >
            {/* Header */}
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-xl bg-primary/20 flex items-center justify-center">
                  <Shield className="w-4 h-4 text-primary" />
                </div>
                <h2 className="font-semibold">Welcome to RIDES4U</h2>
              </div>
              <button
                onClick={skip}
                className="w-8 h-8 rounded-xl bg-secondary border border-border hover:border-rose-400/50 hover:bg-rose-500/10 hover:text-rose-400 flex items-center justify-center transition-all"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Progress */}
            <div className="flex items-center gap-1 px-4 py-2">
              {slides.map((_, index) => (
                <div
                  key={index}
                  className={`h-1 flex-1 rounded-full transition-all ${
                    index <= currentSlide ? "bg-primary" : "bg-border"
                  }`}
                />
              ))}
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto p-6">
              <AnimatePresence mode="wait">
                <motion.div
                  key={currentSlide}
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -20 }}
                  transition={{ duration: 0.2 }}
                  className="space-y-4"
                >
                  {/* Icon */}
                  <div className="w-20 h-20 rounded-2xl bg-secondary flex items-center justify-center mx-auto">
                    {slides[currentSlide].icon}
                  </div>

                  {/* Title */}
                  <h3 className="text-xl font-bold text-center">
                    {slides[currentSlide].title}
                  </h3>

                  {/* Description */}
                  <p className="text-sm text-muted-foreground text-center leading-relaxed">
                    {slides[currentSlide].description}
                  </p>

                  {/* Features */}
                  {slides[currentSlide].features && (
                    <div className="space-y-2 pt-2">
                      {slides[currentSlide].features?.map((feature, idx) => (
                        <div
                          key={idx}
                          className="flex items-center gap-3 p-3 rounded-xl bg-secondary/50 border border-border"
                        >
                          <div className="w-6 h-6 rounded-full bg-primary/20 flex items-center justify-center flex-shrink-0">
                            <Check className="w-3 h-3 text-primary" />
                          </div>
                          <span className="text-sm font-medium">{feature}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </motion.div>
              </AnimatePresence>
            </div>

            {/* Footer */}
            <div className="p-4 border-t border-border bg-secondary/30">
              <div className="flex items-center justify-between gap-3">
                <button
                  onClick={prevSlide}
                  disabled={currentSlide === 0}
                  className={`px-4 py-2 rounded-xl border border-border transition-all ${
                    currentSlide === 0
                      ? "opacity-50 cursor-not-allowed"
                      : "hover:bg-secondary"
                  }`}
                >
                  <ChevronLeft className="w-5 h-5" />
                </button>

                <span className="text-sm text-muted-foreground">
                  {currentSlide + 1} / {slides.length}
                </span>

                <Btn
                  onClick={nextSlide}
                  className="flex-1"
                >
                  {currentSlide === slides.length - 1 ? (
                    <span className="flex items-center gap-2">
                      Get Started <Check className="w-4 h-4" />
                    </span>
                  ) : (
                    <span className="flex items-center gap-2">
                      Next <ChevronRight className="w-4 h-4" />
                    </span>
                  )}
                </Btn>
              </div>

              {!isFirstTime && (
                <button
                  onClick={skip}
                  className="mt-3 w-full text-sm text-muted-foreground hover:text-foreground transition-colors"
                >
                  Skip tour
                </button>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

// Button to trigger onboarding
export function OverviewButton({ userType }: { userType: "driver" | "passenger" }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      <button
        onClick={() => setIsOpen(true)}
        className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-secondary border border-border hover:border-primary/50 hover:bg-muted active:scale-[0.98] transition-all duration-200 text-sm font-medium"
      >
        <Shield className="w-4 h-4 text-primary" />
        <span>App Overview</span>
      </button>

      <OnboardingModal
        isOpen={isOpen}
        onClose={() => setIsOpen(false)}
        userType={userType}
        isFirstTime={false}
      />
    </>
  );
}
