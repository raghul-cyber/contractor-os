from app.modules.profiler.synthesizer import ProfileModel
from app.core.logger import get_logger

logger = get_logger(__name__)

def score_fit(profile: ProfileModel, targets_cfg) -> float:
    """
    Computes a deterministic fit score (0.0 to 1.0) without LLM calls.
    
    Approach:
    1. Extracts target `pain_signals` from targets.yaml.
    2. Combines profile's `pain_points` and `personalization_hooks` into a single lowercase text block.
    3. Iterates through all pain_signals (lowercased). If a signal is a substring of the text block, it counts as a match.
    4. Calculates the score as: (number of matched signals) / (total signals)
       Wait, if they match 1 out of 5 it's 0.2? The default threshold is 0.3.
       We should cap the score at 1.0, but maybe just matching ANY 1 signal gives 0.33, 2 gives 0.66?
       Let's say each matched signal adds 0.34 to the score, capped at 1.0. 
       This ensures matching 1 signal crosses the 0.3 min_fit_score threshold, 
       and 3 matches gives a perfect 1.0.
    """
    try:
        pain_signals = targets_cfg.targeting.pain_signals
    except AttributeError:
        pain_signals = []
        
    if not pain_signals:
        return 1.0 # If no targeting signals configured, everything is a fit
        
    # Combine relevant profile texts
    profile_texts = []
    if getattr(profile, "pain_points", None):
        profile_texts.extend(profile.pain_points)
    if getattr(profile, "personalization_hooks", None):
        profile_texts.extend(profile.personalization_hooks)
        
    combined_text = " ".join(profile_texts).lower()
    
    matches = 0
    for signal in pain_signals:
        if signal.lower() in combined_text:
            matches += 1
            
    score = min(1.0, matches * 0.34)
    return round(score, 2)
