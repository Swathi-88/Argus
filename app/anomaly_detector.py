import random
import numpy as np
from typing import List, Dict, Any, Tuple, Optional
from sklearn.ensemble import IsolationForest
from sqlalchemy.orm import Session

from app.models import Customer, Event, AuditLog
from app.risk_engine import HIGH_RISK_COUNTRIES


class TransactionAnomalyDetector:
    """
    Anomaly Detection Layer:
    Uses scikit-learn IsolationForest models trained per customer segment (Corporate vs Individual)
    on synthetic transaction streams with injected known anomalies.
    
    Feeds detected transaction anomalies into the Bayesian risk engine as TXN_ANOMALY events.
    """
    def __init__(self, contamination: float = 0.05, random_state: int = 42):
        self.contamination = contamination
        self.random_state = random_state
        self.models: Dict[str, IsolationForest] = {}
        self.is_trained: bool = False
        self._initialize_and_train_models()

    def generate_synthetic_transactions(
        self,
        segment: str = "Corporate",
        n_samples: int = 500,
        anomaly_ratio: float = 0.05
    ) -> Tuple[np.ndarray, np.ndarray, List[Dict[str, Any]]]:
        """
        Generates synthetic transaction streams containing normal patterns + injected known anomalies.
        
        Returns:
            X (features matrix), y_true (ground truth: 1 for normal, -1 for anomaly), transaction_records
        """
        np.random.seed(self.random_state)
        random.seed(self.random_state)

        n_anomalies = int(n_samples * anomaly_ratio)
        n_normal = n_samples - n_anomalies

        records = []
        features = []
        y_true = []

        is_corp = (segment.upper() == "CORPORATE")
        base_amount_mean = 25_000.0 if is_corp else 1_500.0
        base_amount_std = 12_000.0 if is_corp else 800.0

        # 1. Normal Transactions
        for i in range(n_normal):
            amount = max(50.0, np.random.normal(base_amount_mean, base_amount_std))
            velocity = int(np.random.poisson(lam=3 if is_corp else 1)) + 1
            dev_turnover = max(0.1, np.random.normal(1.0, 0.3))
            country = np.random.choice(["GB", "US", "DE", "FR", "SG", "CH"], p=[0.5, 0.2, 0.1, 0.1, 0.05, 0.05])
            country_flag = 1.0 if country in HIGH_RISK_COUNTRIES else 0.0

            features.append([amount, velocity, dev_turnover, country_flag])
            y_true.append(1)  # 1 = Normal
            records.append({
                "transaction_id": f"TXN_{i+1:05d}",
                "amount": round(amount, 2),
                "velocity_24h": velocity,
                "deviation_from_turnover": round(dev_turnover, 2),
                "counterparty_country": country,
                "is_ground_truth_anomaly": False
            })

        # 2. Injected Anomalies (Ground Truth = -1)
        anomaly_types = ["STRUCTURING_SPIKE", "HIGH_RISK_JURISDICTION_BURST", "TURNOVER_DEVIATION_EXTREME"]
        for j in range(n_anomalies):
            atype = random.choice(anomaly_types)
            if atype == "STRUCTURING_SPIKE":
                amount = np.random.uniform(9_500.0, 9_990.0)
                velocity = np.random.randint(15, 30)
                dev_turnover = np.random.uniform(3.5, 8.0)
                country = "GB"
            elif atype == "HIGH_RISK_JURISDICTION_BURST":
                amount = base_amount_mean * np.random.uniform(5.0, 15.0)
                velocity = np.random.randint(5, 12)
                dev_turnover = np.random.uniform(4.0, 10.0)
                country = random.choice(list(HIGH_RISK_COUNTRIES))
            else:  # TURNOVER_DEVIATION_EXTREME
                amount = base_amount_mean * np.random.uniform(10.0, 30.0)
                velocity = np.random.randint(2, 6)
                dev_turnover = np.random.uniform(10.0, 25.0)
                country = "PA"

            country_flag = 1.0 if country in HIGH_RISK_COUNTRIES else 0.0
            features.append([amount, velocity, dev_turnover, country_flag])
            y_true.append(-1)  # -1 = Anomaly
            records.append({
                "transaction_id": f"TXN_ANOM_{j+1:05d}",
                "amount": round(amount, 2),
                "velocity_24h": velocity,
                "deviation_from_turnover": round(dev_turnover, 2),
                "counterparty_country": country,
                "is_ground_truth_anomaly": True,
                "anomaly_type": atype
            })

        X = np.array(features)
        y_true = np.array(y_true)
        return X, y_true, records

    def _initialize_and_train_models(self):
        """
        Trains IsolationForest models per segment (Corporate and Individual).
        """
        for segment in ["Corporate", "Individual"]:
            X_train, y_train, _ = self.generate_synthetic_transactions(segment=segment, n_samples=800, anomaly_ratio=self.contamination)
            model = IsolationForest(
                contamination=self.contamination,
                random_state=self.random_state,
                n_estimators=100
            )
            model.fit(X_train)
            self.models[segment] = model

        self.is_trained = True

    def detect_anomalies_in_batch(
        self,
        customer: Customer,
        transactions: List[Dict[str, Any]],
        db: Session,
        auto_trigger_event: bool = True
    ) -> Dict[str, Any]:
        """
        Evaluates a batch of customer transactions against the trained IsolationForest model.
        Identifies anomalies and optionally creates TXN_ANOMALY events in the risk pipeline.
        """
        segment = "Corporate" if (customer.type or "").upper() == "CORPORATE" else "Individual"
        model = self.models.get(segment) or self.models["Corporate"]

        features = []
        for txn in transactions:
            amt = float(txn.get("amount", 0.0))
            vel = int(txn.get("velocity_24h", 1))
            dev = float(txn.get("deviation_from_turnover", 1.0))
            cty = str(txn.get("counterparty_country", "GB")).upper()
            cty_flag = 1.0 if cty in HIGH_RISK_COUNTRIES else 0.0
            features.append([amt, vel, dev, cty_flag])

        if not features:
            return {"customer_id": customer.id, "evaluated": 0, "anomalies_detected": 0, "anomalies": []}

        X = np.array(features)
        preds = model.predict(X)  # 1 = Normal, -1 = Anomaly
        scores = model.decision_function(X)  # lower / negative = more anomalous

        anomalies_found = []
        events_created = []

        for idx, (txn, pred, raw_score) in enumerate(zip(transactions, preds, scores)):
            is_anomaly = (pred == -1)
            # Normalize anomaly score to 0.0 - 1.0 scale
            anomaly_score = round(float(max(0.0, min(1.0, 0.5 - raw_score))), 4)

            if is_anomaly or anomaly_score >= 0.55:
                severity = "CRITICAL" if anomaly_score >= 0.75 else "HIGH"
                anomaly_detail = {
                    "transaction_id": txn.get("transaction_id", f"TXN_{idx+1}"),
                    "amount": txn.get("amount"),
                    "velocity_24h": txn.get("velocity_24h"),
                    "counterparty_country": txn.get("counterparty_country"),
                    "anomaly_score": anomaly_score,
                    "severity": severity,
                    "prediction": "ANOMALOUS"
                }
                anomalies_found.append(anomaly_detail)

                if auto_trigger_event and db is not None:
                    # Inject TXN_ANOMALY event into Event table
                    event = Event(
                        entity_name=customer.name,
                        event_type="TXN_ANOMALY",
                        category="TRANSACTION_ANOMALY",
                        severity=severity,
                        source="IsolationForest_Anomaly_Engine",
                        raw_payload={
                            "anomaly_score": anomaly_score,
                            "transaction_details": txn,
                            "isolation_forest_prediction": "ANOMALOUS"
                        },
                        matched_customer_id=customer.id,
                        match_confidence=100.0,
                        match_method="EXACT"
                    )
                    db.add(event)
                    db.commit()
                    db.refresh(event)
                    events_created.append(event.id)

                    # Log audit entry
                    audit = AuditLog(
                        entity_type="EVENT",
                        entity_id=event.id,
                        action="TRANSACTION_ANOMALY_DETECTED",
                        details={
                            "customer_id": customer.id,
                            "anomaly_score": anomaly_score,
                            "severity": severity,
                            "transaction": txn
                        }
                    )
                    db.add(audit)
                    db.commit()

        return {
            "customer_id": customer.id,
            "segment_model": segment,
            "evaluated_count": len(transactions),
            "anomalies_detected": len(anomalies_found),
            "anomalies": anomalies_found,
            "event_ids_created": events_created
        }


# Global instance
anomaly_detector = TransactionAnomalyDetector()
