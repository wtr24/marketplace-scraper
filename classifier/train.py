"""
EfficientNet-B0 training script for Patagonia Synchilla / Snap-T classifier.

Usage (local machine only):
    pip install -r classifier/requirements-training.txt
    python -m classifier.train

Outputs:
    classifier/fleece_classifier.onnx
"""
import json
import logging
import os
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
from torchvision import transforms
import timm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WANT_DIR = Path("data/classifier/want")
DONT_WANT_DIR = Path("data/classifier/dont_want")
LABELS_FILE = Path("data/classifier/labels.json")
OUTPUT_ONNX = Path("classifier/fleece_classifier.onnx")
EPOCHS_PHASE1 = 15
EPOCHS_PHASE2 = 20
EARLY_STOP_PATIENCE = 5
BATCH_SIZE = 32
THRESHOLD = 0.35


# ─── Dataset ─────────────────────────────────────────────────────────────────

class FleeceDataset(Dataset):
    def __init__(self, samples: list[tuple[Path, int]], transform):
        self.samples = samples
        self.transform = transform

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        img = Image.open(path).convert("RGB")
        return self.transform(img), label


def load_samples() -> tuple[list, list]:
    """Returns (train_samples, val_samples) as lists of (Path, label) tuples."""
    want_files = list(WANT_DIR.glob("*.jpg")) + list(WANT_DIR.glob("*.png")) + list(WANT_DIR.glob("*.webp"))
    dont_files = list(DONT_WANT_DIR.glob("*.jpg")) + list(DONT_WANT_DIR.glob("*.png")) + list(DONT_WANT_DIR.glob("*.webp"))

    # Also include user-labelled images from labels.json
    if LABELS_FILE.exists():
        labels = json.loads(LABELS_FILE.read_text())
        for path_str, meta in labels.items():
            p = Path(path_str)
            if not p.exists():
                continue
            if meta["label"] == "want" and meta["source"] == "user":
                want_files.append(p)
            elif meta["label"] == "dont_want" and meta["source"] == "user":
                dont_files.append(p)

    logger.info(f"Dataset: {len(want_files)} WANT, {len(dont_files)} DONT_WANT")
    assert len(want_files) >= 50, f"Too few WANT samples: {len(want_files)}. Run seed_archive.py and label more."
    assert len(dont_files) >= 50, f"Too few DONT_WANT samples: {len(dont_files)}. Run seed_negatives.py."

    # Stratified 80/20 split
    def split(files):
        n = int(len(files) * 0.8)
        idxs = torch.randperm(len(files)).tolist()
        return [files[i] for i in idxs[:n]], [files[i] for i in idxs[n:]]

    want_tr, want_val = split(want_files)
    dont_tr, dont_val = split(dont_files)

    train = [(p, 1) for p in want_tr] + [(p, 0) for p in dont_tr]
    val = [(p, 1) for p in want_val] + [(p, 0) for p in dont_val]
    return train, val


# ─── Transforms ──────────────────────────────────────────────────────────────

TRAIN_TF = transforms.Compose([
    transforms.RandomResizedCrop(224, scale=(0.6, 1.0)),
    transforms.RandomHorizontalFlip(),
    transforms.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.3, hue=0.05),
    transforms.RandomRotation(15),
    transforms.RandomPerspective(distortion_scale=0.2, p=0.3),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])
VAL_TF = transforms.Compose([
    transforms.Resize(256),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


# ─── Training ────────────────────────────────────────────────────────────────

def make_loader(samples, transform, shuffle=False, sampler=None):
    ds = FleeceDataset(samples, transform)
    return DataLoader(ds, batch_size=BATCH_SIZE, shuffle=shuffle, sampler=sampler, num_workers=0)


def train_phase(model, loader, val_loader, optimizer, criterion, epochs, patience, label):
    best_acc, no_improve = 0.0, 0
    for epoch in range(epochs):
        model.train()
        for imgs, labels in loader:
            optimizer.zero_grad()
            loss = criterion(model(imgs), labels)
            loss.backward()
            optimizer.step()

        acc = evaluate(model, val_loader)
        logger.info(f"[{label}] Epoch {epoch+1}/{epochs} val_acc={acc:.3f}")
        if acc > best_acc:
            best_acc = acc
            torch.save(model.state_dict(), str(Path(os.environ.get("TEMP", "/tmp")) / "fleece_best.pt"))
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                logger.info(f"[{label}] Early stop at epoch {epoch+1}")
                break

    model.load_state_dict(torch.load(str(Path(os.environ.get("TEMP", "/tmp")) / "fleece_best.pt")))
    return best_acc


def evaluate(model, loader) -> float:
    model.eval()
    correct = total = 0
    with torch.no_grad():
        for imgs, labels in loader:
            preds = model(imgs).argmax(1)
            correct += (preds == labels).sum().item()
            total += len(labels)
    return correct / total if total > 0 else 0.0


def main():
    train_samples, val_samples = load_samples()

    # Class weights for imbalance
    want_n = sum(1 for _, l in train_samples if l == 1)
    dont_n = sum(1 for _, l in train_samples if l == 0)
    class_weights = torch.tensor([1.0, dont_n / want_n])
    sample_weights = torch.tensor([class_weights[l].item() for _, l in train_samples])
    sampler = WeightedRandomSampler(sample_weights, len(sample_weights))

    train_loader = make_loader(train_samples, TRAIN_TF, sampler=sampler)
    val_loader = make_loader(val_samples, VAL_TF)

    # Model
    model = timm.create_model("efficientnet_b0", pretrained=True, num_classes=0)
    head = nn.Sequential(
        nn.Dropout(0.3), nn.Linear(1280, 256), nn.ReLU(),
        nn.Dropout(0.2), nn.Linear(256, 2)
    )
    model = nn.Sequential(model, head)

    criterion = nn.CrossEntropyLoss(weight=class_weights)

    # Phase 1: frozen backbone
    for p in list(model.parameters())[:-len(list(head.parameters()))]:
        p.requires_grad_(False)
    opt1 = torch.optim.Adam(filter(lambda p: p.requires_grad, model.parameters()), lr=1e-3, weight_decay=1e-4)
    logger.info("Phase 1: training head only")
    train_phase(model, train_loader, val_loader, opt1, criterion, EPOCHS_PHASE1, EARLY_STOP_PATIENCE, "P1")

    # Phase 2: unfreeze top 30%
    all_params = list(model.parameters())
    for p in all_params[int(len(all_params) * 0.7):]:
        p.requires_grad_(True)
    opt2 = torch.optim.Adam([
        {"params": all_params[:int(len(all_params)*0.7)], "lr": 1e-5},
        {"params": all_params[int(len(all_params)*0.7):], "lr": 1e-4},
    ], weight_decay=1e-4)
    logger.info("Phase 2: selective unfreeze")
    final_acc = train_phase(model, train_loader, val_loader, opt2, criterion, EPOCHS_PHASE2, EARLY_STOP_PATIENCE, "P2")

    logger.info(f"Final val accuracy: {final_acc:.3f}")
    if final_acc < 0.80:
        logger.warning("Accuracy below 0.80 — label more data before deploying")

    # Export ONNX
    model.eval()
    dummy = torch.randn(1, 3, 224, 224)
    torch.onnx.export(
        model, dummy, str(OUTPUT_ONNX),
        input_names=["image"], output_names=["logits"],
        dynamic_axes={"image": {0: "batch"}},
        opset_version=17,
    )
    logger.info(f"Exported model to {OUTPUT_ONNX}")
    logger.info("Next: git add classifier/fleece_classifier.onnx && git commit && git push")


if __name__ == "__main__":
    main()
