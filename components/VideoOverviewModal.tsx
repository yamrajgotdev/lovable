import { useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { X, Play } from "lucide-react";

export function VideoOverviewModal({ videoUrl = "https://www.youtube.com/embed/dQw4w9WgXcQ" }: { videoUrl?: string }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="flex items-center justify-center w-9 h-9 rounded-lg bg-secondary text-secondary-foreground hover:bg-accent hover:text-accent-foreground transition-colors press"
        aria-label="Watch overview video"
        title="Watch overview video"
      >
        <Play className="w-4 h-4 fill-current" />
      </button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl w-full p-0 border-0">
          <button
            onClick={() => setOpen(false)}
            className="absolute top-4 right-4 z-10 rounded-lg bg-background/80 backdrop-blur p-2 hover:bg-background transition-colors"
          >
            <X className="w-5 h-5" />
          </button>

          <div className="relative w-full pt-[56.25%] bg-background rounded-lg overflow-hidden">
            <iframe
              className="absolute top-0 left-0 w-full h-full"
              src={videoUrl}
              title="RIDES4U Overview"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
              allowFullScreen
            />
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
